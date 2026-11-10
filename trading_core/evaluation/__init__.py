"""Performance and replay-native evaluation tools."""

from trading_core.evaluation.metrics import (
    compute_max_drawdown_pct,
    compute_sharpe,
    compute_win_rate,
    summarize_performance,
)
from trading_core.evaluation.replay_evaluator import (
    ReplayDecisionEvaluator,
    ReplayEvaluationConfig,
    ReplayEvaluationFinding,
    ReplayEvaluationReport,
)
from trading_core.evaluation.replay_dataset import ReplayDataset, ReplayDatasetManifest, ReplayDatasetStore

__all__ = [
    "ReplayDecisionEvaluator",
    "ReplayEvaluationConfig",
    "ReplayEvaluationFinding",
    "ReplayEvaluationReport",
    "ReplayDataset",
    "ReplayDatasetManifest",
    "ReplayDatasetStore",
    "compute_max_drawdown_pct",
    "compute_sharpe",
    "compute_win_rate",
    "summarize_performance",
]

