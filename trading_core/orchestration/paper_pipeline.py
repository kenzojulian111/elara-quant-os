from __future__ import annotations

from dataclasses import dataclass

from trading_core.execution.adapter import PaperExecutionAdapter
from trading_core.orchestration.portfolio_brain import PortfolioBrain
from trading_core.orchestration.shadow_pipeline import ShadowPipelineConfig, ShadowTradingPipeline


@dataclass
class PaperPipelineConfig(ShadowPipelineConfig):
    """Configuration for the default MVP execution mode.

    Paper state is deliberately separate from the audit ledger: the state file
    is the durable idempotency boundary, while the ledger remains an append-only
    forensic record.
    """

    ledger_path: str = "runtime/paper_ledger.jsonl"
    paper_state_path: str = "runtime/paper_execution_state.json"
    source: str = "paper_pipeline"


class PaperTradingPipeline(ShadowTradingPipeline):
    """The MVP pipeline: governed decisions with durable paper execution."""

    def __init__(self, portfolio_brain: PortfolioBrain, config: PaperPipelineConfig | None = None):
        config = config or PaperPipelineConfig()
        super().__init__(
            portfolio_brain,
            config=config,
            execution_adapter=PaperExecutionAdapter(config.paper_state_path),
        )
        # Keep every execution receipt in the same immutable audit trail as its
        # decision. The adapter receives the ledger after its parent is ready.
        self.execution.ledger = self.ledger
