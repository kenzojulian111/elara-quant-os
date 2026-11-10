from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from trading_core.data_contracts import DecisionEnvelope


@dataclass(frozen=True)
class CognitiveLayerContract:
    name: str
    responsibility: str
    owner_module: str
    trace_step: str = ""
    component_source: str = ""
    event_type: str = ""
    required: bool = True
    depends_on: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Mapping[str, object]:
        return {
            "name": self.name,
            "responsibility": self.responsibility,
            "owner_module": self.owner_module,
            "trace_step": self.trace_step,
            "component_source": self.component_source,
            "event_type": self.event_type,
            "required": bool(self.required),
            "depends_on": tuple(self.depends_on),
        }


@dataclass(frozen=True)
class CognitiveCoherenceFinding:
    code: str
    severity: str
    message: str
    details: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> Mapping[str, object]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class CognitiveCoherenceReport:
    status: str
    score: int
    findings: tuple[CognitiveCoherenceFinding, ...] = ()
    layers: tuple[CognitiveLayerContract, ...] = ()
    metrics: Mapping[str, object] = field(default_factory=dict)

    @property
    def critical_count(self) -> int:
        return sum(1 for finding in self.findings if finding.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for finding in self.findings if finding.severity == "warning")

    def to_dict(self) -> Mapping[str, object]:
        return {
            "status": self.status,
            "score": int(self.score),
            "critical_count": self.critical_count,
            "warning_count": self.warning_count,
            "metrics": dict(self.metrics),
            "findings": tuple(finding.to_dict() for finding in self.findings),
            "layers": tuple(layer.to_dict() for layer in self.layers),
        }


class CognitiveCoherenceChecker:
    def __init__(self, root: str | Path = "."):
        self.root = Path(root)
        self.layers = default_cognitive_layers()

    def build_static_report(self) -> CognitiveCoherenceReport:
        findings: list[CognitiveCoherenceFinding] = []
        missing = tuple(layer.owner_module for layer in self.layers if not (self.root / layer.owner_module).exists())
        if missing:
            findings.append(
                CognitiveCoherenceFinding(
                    code="cognitive_layer_owner_missing",
                    severity="critical",
                    message="One or more cognitive layer owner modules are missing.",
                    details={"missing": missing},
                )
            )
        trace_steps = tuple(layer.trace_step for layer in self.layers if layer.trace_step)
        duplicate_trace_steps = _duplicates(trace_steps)
        if duplicate_trace_steps:
            findings.append(
                CognitiveCoherenceFinding(
                    code="duplicate_cognitive_trace_steps",
                    severity="warning",
                    message="Cognitive layer trace steps should identify a single responsibility.",
                    details={"trace_steps": duplicate_trace_steps},
                )
            )
        layer_names = {layer.name for layer in self.layers}
        unknown_dependencies = tuple(
            f"{layer.name}->{dependency}"
            for layer in self.layers
            for dependency in layer.depends_on
            if dependency not in layer_names
        )
        if unknown_dependencies:
            findings.append(
                CognitiveCoherenceFinding(
                    code="unknown_cognitive_layer_dependency",
                    severity="critical",
                    message="Cognitive layer dependency references an unknown layer.",
                    details={"dependencies": unknown_dependencies},
                )
            )
        return _report(
            findings,
            layers=self.layers,
            metrics={
                "layer_count": len(self.layers),
                "missing_owner_modules": len(missing),
                "trace_step_count": len(trace_steps),
                "required_layer_count": sum(1 for layer in self.layers if layer.required),
                "optional_layer_count": sum(1 for layer in self.layers if not layer.required),
            },
        )

    def evaluate_envelope(self, envelope: DecisionEnvelope) -> CognitiveCoherenceReport:
        findings: list[CognitiveCoherenceFinding] = []
        trace_steps = tuple(step.step for step in envelope.pipeline_trace)
        trace_step_set = set(trace_steps)
        reasoned = envelope.reasoned_decision
        components = tuple(reasoned.components if reasoned else ())
        component_sources = {component.source for component in components}

        if not envelope.trace_id:
            findings.append(_critical("missing_trace_id", "Decision envelope has no trace_id."))
        if not envelope.pipeline_trace:
            findings.append(_critical("missing_pipeline_trace", "Decision envelope has no pipeline trace."))
        if "signal" not in trace_step_set:
            findings.append(_critical("missing_signal_trace", "Decision pipeline did not record the strategy signal step."))
        if reasoned is None:
            findings.append(_critical("missing_reasoned_decision", "Decision envelope has no final reasoned decision."))
            return _report(findings, layers=self.layers, metrics={"trace_steps": trace_steps, "component_sources": tuple()})

        if "strategy" not in component_sources:
            findings.append(
                _critical("missing_strategy_component", "Reasoned decision does not include the strategy component.")
            )
        if "reasoning" not in trace_step_set:
            findings.append(
                CognitiveCoherenceFinding(
                    code="missing_reasoning_trace",
                    severity="warning",
                    message="Final reasoning synthesis should be recorded as the last pipeline trace step.",
                    details={"trace_steps": trace_steps},
                )
            )
        elif trace_steps[-1] != "reasoning":
            findings.append(
                _critical(
                    "reasoning_trace_not_final",
                    "Final reasoning synthesis must close the explainable decision trace.",
                    {"trace_steps": trace_steps},
                )
            )
        for layer in self.layers:
            if not layer.trace_step or not layer.component_source:
                continue
            if layer.trace_step in trace_step_set and layer.component_source not in component_sources:
                findings.append(
                    CognitiveCoherenceFinding(
                        code=f"trace_without_component_{layer.name}",
                        severity="warning",
                        message="A traced cognitive layer was not represented in the final reasoning components.",
                        details={"trace_step": layer.trace_step, "component_source": layer.component_source},
                    )
                )

        empty_reason_steps = tuple(step.step for step in envelope.pipeline_trace if not step.reasons)
        if empty_reason_steps:
            findings.append(
                CognitiveCoherenceFinding(
                    code="trace_steps_without_reasons",
                    severity="warning",
                    message="Every cognitive trace step should carry at least one explanation reason.",
                    details={"steps": empty_reason_steps},
                )
            )

        scores = dict(reasoned.component_scores or {})
        missing_scores = tuple(
            key for key in ("probability", "uncertainty", "expected_utility", "probability_model", "bayesian_posterior")
            if key not in scores
        )
        if missing_scores:
            findings.append(
                CognitiveCoherenceFinding(
                    code="missing_probabilistic_scores",
                    severity="warning",
                    message="Reasoned decision is missing probabilistic explainability fields.",
                    details={"missing": missing_scores},
                )
            )

        if not reasoned.approved and not reasoned.constraints:
            findings.append(
                CognitiveCoherenceFinding(
                    code="blocked_decision_without_constraints",
                    severity="warning",
                    message="Blocked decisions should expose constraints explaining the veto.",
                    details={"action": reasoned.action, "status": envelope.status},
                )
            )

        if reasoned.approved and reasoned.action in {"enter_long", "enter_short"} and envelope.execution_plan is None:
            findings.append(
                _critical(
                    "approved_entry_without_execution_plan",
                    "Approved entry decision has no execution plan.",
                    {"action": reasoned.action, "status": envelope.status},
                )
            )

        if "governance" in trace_step_set and "runtime_mode" not in trace_step_set:
            findings.append(
                CognitiveCoherenceFinding(
                    code="governance_without_runtime_trace",
                    severity="warning",
                    message="Governance was traced without runtime mode context.",
                    details={"trace_steps": trace_steps},
                )
            )

        if "runtime_mode" in trace_step_set and "governance" in trace_step_set:
            if trace_steps.index("runtime_mode") > trace_steps.index("governance"):
                findings.append(
                    _critical(
                        "runtime_after_governance",
                        "Runtime mode must be known before governance is evaluated.",
                        {"trace_steps": trace_steps},
                    )
                )

        return _report(
            findings,
            layers=self.layers,
            metrics={
                "trace_steps": trace_steps,
                "component_sources": tuple(sorted(component_sources)),
                "reasoned_action": reasoned.action,
                "approved": bool(reasoned.approved),
            },
        )


def default_cognitive_layers() -> tuple[CognitiveLayerContract, ...]:
    return (
        CognitiveLayerContract(
            "portfolio",
            "Select strategy allocation and concentration posture.",
            "trading_core/orchestration/portfolio_brain.py",
            event_type="portfolio",
            component_source="portfolio",
            required=False,
        ),
        CognitiveLayerContract(
            "attention",
            "Focus compute and strategy attention under runtime pressure.",
            "trading_core/orchestration/attention_engine.py",
            trace_step="attention",
            event_type="attention",
            depends_on=("portfolio",),
            required=False,
        ),
        CognitiveLayerContract(
            "objective",
            "Translate operator and runtime intent into decision bias.",
            "trading_core/orchestration/objective_engine.py",
            trace_step="objective",
            component_source="objective",
            event_type="objective",
            depends_on=("portfolio", "runtime"),
            required=False,
        ),
        CognitiveLayerContract(
            "temporal",
            "Apply short-horizon memory, regime persistence, and recent outcome context.",
            "trading_core/orchestration/temporal_reasoner.py",
            trace_step="temporal",
            component_source="temporal",
            event_type="temporal",
            depends_on=("portfolio",),
            required=False,
        ),
        CognitiveLayerContract(
            "strategy",
            "Emit the raw strategy signal before governance or risk mutation.",
            "trading_core/orchestration/decision_core.py",
            trace_step="signal",
            component_source="strategy",
        ),
        CognitiveLayerContract(
            "risk",
            "Validate position sizing, drawdown, and execution guardrails.",
            "trading_core/risk/risk_engine.py",
            trace_step="risk_gate",
            component_source="risk",
            depends_on=("strategy",),
            required=False,
        ),
        CognitiveLayerContract(
            "runtime",
            "Treat system health and dependency degradation as decision input.",
            "utils/health.py",
            trace_step="runtime_mode",
            component_source="runtime",
            depends_on=("strategy",),
            required=False,
        ),
        CognitiveLayerContract(
            "governance",
            "Apply operator, dependency, market, and runtime permission gates.",
            "trading_core/orchestration/decision_governor.py",
            trace_step="governance",
            component_source="governance",
            event_type="governance",
            depends_on=("runtime",),
            required=False,
        ),
        CognitiveLayerContract(
            "reasoning",
            "Fuse components into final explainable, probabilistic decision.",
            "trading_core/orchestration/decision_reasoner.py",
            trace_step="reasoning",
            depends_on=("strategy", "risk", "runtime", "governance"),
        ),
    )


def _report(
    findings: Iterable[CognitiveCoherenceFinding],
    *,
    layers: tuple[CognitiveLayerContract, ...],
    metrics: Mapping[str, object],
) -> CognitiveCoherenceReport:
    findings_tuple = tuple(findings)
    critical_count = sum(1 for finding in findings_tuple if finding.severity == "critical")
    warning_count = sum(1 for finding in findings_tuple if finding.severity == "warning")
    status = "critical" if critical_count else "warning" if warning_count else "ok"
    score = max(0, 100 - critical_count * 35 - warning_count * 10)
    return CognitiveCoherenceReport(status=status, score=score, findings=findings_tuple, layers=layers, metrics=metrics)


def _critical(code: str, message: str, details: Mapping[str, object] | None = None) -> CognitiveCoherenceFinding:
    return CognitiveCoherenceFinding(code=code, severity="critical", message=message, details=dict(details or {}))


def _duplicates(items: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in items:
        if item in seen:
            duplicates.add(item)
        seen.add(item)
    return tuple(sorted(duplicates))
