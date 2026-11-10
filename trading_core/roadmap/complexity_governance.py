from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ComplexityFinding:
    code: str
    severity: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ComplexityDomainReport:
    domain: str
    score: int
    findings: tuple[ComplexityFinding, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        if any(finding.severity == "critical" for finding in self.findings):
            return "critical"
        if any(finding.severity == "warning" for finding in self.findings):
            return "warning"
        return "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "status": self.status,
            "score": int(self.score),
            "metrics": dict(self.metrics),
            "findings": tuple(finding.to_dict() for finding in self.findings),
        }


@dataclass(frozen=True)
class ComplexityGovernanceReport:
    status: str
    score: int
    domains: tuple[ComplexityDomainReport, ...]
    summary: str

    @property
    def findings(self) -> tuple[ComplexityFinding, ...]:
        return tuple(finding for domain in self.domains for finding in domain.findings)

    @property
    def critical_count(self) -> int:
        return sum(1 for finding in self.findings if finding.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for finding in self.findings if finding.severity == "warning")

    def domain(self, name: str) -> ComplexityDomainReport:
        normalized = str(name or "").strip().lower()
        for domain in self.domains:
            if domain.domain.lower() == normalized:
                return domain
        raise KeyError(name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "score": int(self.score),
            "summary": self.summary,
            "critical_count": self.critical_count,
            "warning_count": self.warning_count,
            "domains": tuple(domain.to_dict() for domain in self.domains),
        }


class ComplexityGovernanceChecker:
    CONFIG_DOMAIN_EXAMPLES = (
        "config/core.env.example",
        "config/runtime.env.example",
        "config/trading.env.example",
        "config/governance.env.example",
        "config/observability.env.example",
        "config/llm.env.example",
        "config/blockchain.env.example",
        "config/experimental.env.example",
    )
    REQUIRED_DOCS = (
        "ELARA_THEORY.md",
        "CORE_IDENTITY.md",
        "docs/architecture_doctrine.md",
        "docs/complexity_governance.md",
        "docs/config_governance.md",
        "docs/cognitive_coherence.md",
        "docs/cognition_mesh.md",
        "docs/decision_flow/decision_topology.md",
        "docs/decision_flow/event_ledger_contract.md",
        "docs/decision_flow/event_sourcing.md",
        "docs/decision_flow/redis_stream_bus.md",
        "docs/decision_flow/lifecycle_events.md",
        "docs/probabilistic_cognition.md",
        "docs/protocols/core_contracts.md",
        "docs/replay_native_evaluation.md",
        "docs/system/architecture_heatmap.md",
        "docs/system/cognitive_boundaries.md",
        "docs/subsystems/constitution.md",
        "docs/runtime/causal_analysis.md",
        "docs/runtime/state_inspection.md",
        "docs/observability_q1.md",
        "docs/year1_completion.md",
        "docs/year1_q1_q3_execution.md",
        "docs/year1_q4_year2_q4_execution.md",
        "docs/year3_year4_execution.md",
    )
    REQUIRED_PROTOCOL_ANCHORS = (
        "config/governance.py",
        "trading_core/data_contracts.py",
        "trading_core/protocols/core_contracts.py",
        "trading_core/coordination/event_types.py",
        "trading_core/coordination/event_stream.py",
        "trading_core/coordination/event_sourcing.py",
        "trading_core/coordination/event_ledger.py",
        "trading_core/coordination/decision_lifecycle.py",
        "trading_core/evaluation/replay_evaluator.py",
        "trading_core/evaluation/replay_dataset.py",
        "trading_core/intelligence/confidence.py",
        "trading_core/intelligence/calibration.py",
        "trading_core/intelligence/runtime_causal.py",
        "trading_core/coordination/external_bus.py",
        "trading_core/coordination/cqrs.py",
        "trading_core/coordination/snapshot_replay.py",
        "trading_core/governance/engine_v2.py",
        "trading_core/roadmap/architecture_heatmap.py",
        "trading_core/roadmap/cognitive_coherence.py",
        "trading_core/roadmap/decision_topology.py",
        "trading_core/roadmap/subsystem_constitution.py",
        "runtime/capabilities.py",
        "utils/service_contracts.py",
    )
    REQUIRED_REPLAY_ANCHORS = (
        "trading_core/market/replay_engine.py",
        "trading_core/simulation/fault_injection.py",
        "trading_core/simulation/macro.py",
        "trading_core/simulation/scenario_runner.py",
        "trading_core/simulation/black_swan.py",
        "trading_core/simulation/latency_lab.py",
    )
    FRONTIER_ANCHORS = {
        "probabilistic_intelligence": (
            ("trading_core/intelligence/probabilistic.py", "probability baseline"),
            ("trading_core/intelligence/bayesian.py", "Bayesian inference"),
            ("trading_core/intelligence/causal_graph.py", "causal graph"),
        ),
        "event_driven_maturity": (
            ("trading_core/coordination/event_stream.py", "local event stream"),
            ("trading_core/coordination/event_ledger.py", "local event ledger"),
            ("trading_core/coordination/external_bus.py", "external bus adapter"),
        ),
        "rust_runtime": (
            ("crates/elara_runtime/Cargo.toml", "Rust runtime crate"),
            ("trading_core/runtime/rust_gateway.py", "Python/Rust gateway"),
        ),
        "replay_simulation": (
            ("trading_core/simulation/fault_injection.py", "fault injection"),
            ("trading_core/simulation/black_swan.py", "black swan replay"),
            ("trading_core/simulation/latency_lab.py", "latency simulation"),
        ),
        "distributed_cognition": (
            ("trading_core/cognition/reasoning_node.py", "reasoning node"),
            ("trading_core/cognition/memory_node.py", "memory node"),
            ("trading_core/cognition/compute_router.py", "adaptive compute router"),
        ),
        "world_modeling": (
            ("trading_core/simulation/macro.py", "macro shock simulation"),
            ("trading_core/world_model/causal_macro_graph.py", "macro causal graph"),
            ("trading_core/world_model/capital_flow.py", "capital flow simulation"),
        ),
        "blockchain_intelligence": (
            ("trading_core/blockchain/mempool.py", "mempool analysis"),
            ("trading_core/blockchain/wallet_graph.py", "wallet graph intelligence"),
            ("trading_core/blockchain/governance.py", "on-chain governance intelligence"),
        ),
    }

    ENV_KEY_WARNING_BUDGET = 420
    ENV_KEY_CRITICAL_BUDGET = 650
    TYPE_ESCAPE_WARNING_BUDGET = 260
    PYTHON_FILE_WARNING_BUDGET = 360

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def build_report(self) -> ComplexityGovernanceReport:
        domains = (
            self._config_surface(),
            self._documentation_structure(),
            self._protocol_formalization(),
            self._replay_and_event_maturity(),
            self._codebase_size(),
            self._frontier_gap_tracker(),
        )
        critical_count = sum(1 for domain in domains for finding in domain.findings if finding.severity == "critical")
        warning_count = sum(1 for domain in domains for finding in domain.findings if finding.severity == "warning")
        status = "critical" if critical_count else "warning" if warning_count else "ok"
        score = max(0, 100 - critical_count * 25 - warning_count * 4)
        summary = (
            "Complexity governance has critical contract gaps."
            if critical_count
            else "Complexity governance is intact with tracked maturity gaps."
            if warning_count
            else "Complexity governance is clean."
        )
        return ComplexityGovernanceReport(status=status, score=score, domains=domains, summary=summary)

    def _config_surface(self) -> ComplexityDomainReport:
        findings: list[ComplexityFinding] = []
        env_keys = self._env_key_count(self.root / ".env.example")
        missing_domains = self._missing(self.CONFIG_DOMAIN_EXAMPLES)

        if missing_domains:
            findings.append(
                ComplexityFinding(
                    code="config_domain_examples_missing",
                    severity="critical",
                    message="Config domain examples are missing; root env entropy cannot be split safely.",
                    details={"missing": missing_domains},
                )
            )
        if env_keys > self.ENV_KEY_CRITICAL_BUDGET:
            findings.append(
                ComplexityFinding(
                    code="config_env_surface_critical",
                    severity="critical",
                    message=".env.example has exceeded the critical config surface budget.",
                    details={"env_keys": env_keys, "budget": self.ENV_KEY_CRITICAL_BUDGET},
                )
            )
        elif env_keys > self.ENV_KEY_WARNING_BUDGET:
            findings.append(
                ComplexityFinding(
                    code="config_env_surface_large",
                    severity="warning",
                    message=".env.example is still large; continue migrating active config into domain files.",
                    details={"env_keys": env_keys, "budget": self.ENV_KEY_WARNING_BUDGET},
                )
            )

        required = ("config/governance.py", "config/pydantic_settings_schema.py")
        missing_required = self._missing(required)
        if missing_required:
            findings.append(
                ComplexityFinding(
                    code="config_governance_contract_missing",
                    severity="critical",
                    message="Config governance contract files are missing.",
                    details={"missing": missing_required},
                )
            )
        return self._domain_report(
            "config_surface",
            findings,
            {"env_keys": env_keys, "domain_examples": len(self.CONFIG_DOMAIN_EXAMPLES) - len(missing_domains)},
        )

    def _documentation_structure(self) -> ComplexityDomainReport:
        missing_docs = self._missing(self.REQUIRED_DOCS)
        findings = []
        if missing_docs:
            findings.append(
                ComplexityFinding(
                    code="architecture_docs_missing",
                    severity="critical",
                    message="Required architecture and roadmap documents are missing.",
                    details={"missing": missing_docs},
                )
            )
        return self._domain_report(
            "documentation_structure",
            findings,
            {"required_docs": len(self.REQUIRED_DOCS), "missing_docs": len(missing_docs)},
        )

    def _protocol_formalization(self) -> ComplexityDomainReport:
        findings: list[ComplexityFinding] = []
        missing_protocols = self._missing(self.REQUIRED_PROTOCOL_ANCHORS)
        if missing_protocols:
            findings.append(
                ComplexityFinding(
                    code="protocol_contracts_missing",
                    severity="critical",
                    message="Internal protocol contracts are missing.",
                    details={"missing": missing_protocols},
                )
            )

        type_escape_count = self._pattern_count(
            ("apps", "config", "core", "runtime", "trading_core", "utils"),
            re.compile(r"\bdict\[(?:str, )?(?:Any|object)\]|\bAny\b"),
        )
        if type_escape_count > self.TYPE_ESCAPE_WARNING_BUDGET:
            findings.append(
                ComplexityFinding(
                    code="type_escape_surface_large",
                    severity="warning",
                    message="Typed contracts exist, but broad Any/dict escape hatches are still common.",
                    details={"type_escape_count": type_escape_count, "budget": self.TYPE_ESCAPE_WARNING_BUDGET},
                )
            )

        return self._domain_report(
            "protocol_formalization",
            findings,
            {
                "required_protocol_anchors": len(self.REQUIRED_PROTOCOL_ANCHORS),
                "missing_protocol_anchors": len(missing_protocols),
                "type_escape_count": type_escape_count,
            },
        )

    def _replay_and_event_maturity(self) -> ComplexityDomainReport:
        missing = self._missing(self.REQUIRED_REPLAY_ANCHORS)
        findings = []
        if missing:
            findings.append(
                ComplexityFinding(
                    code="replay_event_anchors_missing",
                    severity="critical",
                    message="Replay and event maturity anchors are missing.",
                    details={"missing": missing},
                )
            )
        return self._domain_report(
            "replay_event_maturity",
            findings,
            {"required_replay_anchors": len(self.REQUIRED_REPLAY_ANCHORS), "missing_replay_anchors": len(missing)},
        )

    def _codebase_size(self) -> ComplexityDomainReport:
        python_files = tuple(self.root.rglob("*.py"))
        filtered = tuple(
            path
            for path in python_files
            if not any(part in {"__pycache__", ".venv", "runtime", "logs", "build"} for part in path.parts)
        )
        findings: list[ComplexityFinding] = []
        if len(filtered) > self.PYTHON_FILE_WARNING_BUDGET:
            findings.append(
                ComplexityFinding(
                    code="python_file_surface_large",
                    severity="warning",
                    message="Python module count is growing; keep subsystem ownership and docs current.",
                    details={"python_files": len(filtered), "budget": self.PYTHON_FILE_WARNING_BUDGET},
                )
            )
        return self._domain_report("codebase_size", findings, {"python_files": len(filtered)})

    def _frontier_gap_tracker(self) -> ComplexityDomainReport:
        findings: list[ComplexityFinding] = []
        metrics: dict[str, Any] = {}
        for domain, anchors in self.FRONTIER_ANCHORS.items():
            present = tuple(label for path, label in anchors if (self.root / path).exists())
            missing = tuple(label for path, label in anchors if not (self.root / path).exists())
            metrics[domain] = {"present": present, "missing": missing}
            if missing:
                findings.append(
                    ComplexityFinding(
                        code=f"{domain}_tracked_gap",
                        severity="warning",
                        message=f"{domain.replace('_', ' ').title()} is not mature yet; keep it tracked instead of pretending it is done.",
                        details={"present": present, "missing": missing},
                    )
                )
        return self._domain_report("frontier_gap_tracker", findings, metrics)

    def _domain_report(
        self,
        domain: str,
        findings: list[ComplexityFinding],
        metrics: dict[str, Any],
    ) -> ComplexityDomainReport:
        critical_count = sum(1 for finding in findings if finding.severity == "critical")
        warning_count = sum(1 for finding in findings if finding.severity == "warning")
        score = max(0, 100 - critical_count * 35 - warning_count * 10)
        return ComplexityDomainReport(domain=domain, score=score, findings=tuple(findings), metrics=metrics)

    def _missing(self, paths: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(path for path in paths if not (self.root / path).exists())

    @staticmethod
    def _env_key_count(path: Path) -> int:
        if not path.exists():
            return 0
        pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if pattern.match(line.strip()))

    def _pattern_count(self, roots: tuple[str, ...], pattern: re.Pattern[str]) -> int:
        count = 0
        for root_name in roots:
            root_path = self.root / root_name
            if not root_path.exists():
                continue
            for path in root_path.rglob("*.py"):
                if any(part in {"__pycache__", ".venv"} for part in path.parts):
                    continue
                try:
                    count += len(pattern.findall(path.read_text(encoding="utf-8")))
                except UnicodeDecodeError:
                    continue
        return count
