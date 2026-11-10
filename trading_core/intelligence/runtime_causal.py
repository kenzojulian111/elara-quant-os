from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from utils.health import RuntimeModeAssessment


@dataclass(frozen=True)
class RuntimeCausalFinding:
    cause: str
    effect: str
    severity: str
    confidence: float
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Mapping[str, object]:
        return {
            "cause": self.cause,
            "effect": self.effect,
            "severity": self.severity,
            "confidence": round(float(self.confidence), 6),
            "reasons": tuple(self.reasons),
        }


@dataclass(frozen=True)
class RuntimeCausalReport:
    runtime_mode: str
    allow_new_entries: bool
    findings: tuple[RuntimeCausalFinding, ...] = field(default_factory=tuple)
    dominant_cause: str = ""

    def to_dict(self) -> Mapping[str, object]:
        return {
            "runtime_mode": self.runtime_mode,
            "allow_new_entries": bool(self.allow_new_entries),
            "dominant_cause": self.dominant_cause,
            "findings": tuple(finding.to_dict() for finding in self.findings),
        }


class RuntimeCausalAnalyzer:
    def analyze(self, assessment: RuntimeModeAssessment) -> RuntimeCausalReport:
        findings: list[RuntimeCausalFinding] = []
        mode = str(assessment.mode or "full")
        impacts = dict(assessment.dependency_impacts or {})
        for dependency, impact in impacts.items():
            state = str((impact or {}).get("state") or assessment.dependency_states.get(dependency) or "")
            impact_label = str((impact or {}).get("impact") or "unknown")
            severity = _severity_for(mode, impact_label, state)
            confidence = _confidence_for(mode, impact_label, state)
            findings.append(
                RuntimeCausalFinding(
                    cause=f"{dependency}:{state or 'unknown'}",
                    effect=impact_label,
                    severity=severity,
                    confidence=confidence,
                    reasons=tuple(assessment.reasons),
                )
            )
        if not findings and mode in {"safe", "degraded"}:
            findings.append(
                RuntimeCausalFinding(
                    cause="runtime_mode",
                    effect=f"mode={mode}",
                    severity="high" if mode == "safe" else "medium",
                    confidence=0.55,
                    reasons=tuple(assessment.reasons),
                )
            )
        dominant = ""
        if findings:
            dominant = sorted(findings, key=lambda item: _severity_rank(item.severity) + item.confidence, reverse=True)[0].cause
        return RuntimeCausalReport(
            runtime_mode=mode,
            allow_new_entries=bool(assessment.allow_new_entries),
            findings=tuple(findings),
            dominant_cause=dominant,
        )


def _severity_for(mode: str, impact: str, state: str) -> str:
    text = f"{mode}:{impact}:{state}".lower()
    if "safe" in text or "core" in text or "down" in text:
        return "high"
    if "degraded" in text or "partial" in text or "support" in text:
        return "medium"
    return "low"


def _confidence_for(mode: str, impact: str, state: str) -> float:
    confidence = 0.35
    text = f"{mode}:{impact}:{state}".lower()
    if "down" in text:
        confidence += 0.30
    if "safe" in text:
        confidence += 0.25
    if "degraded" in text or "partial" in text:
        confidence += 0.15
    return min(1.0, confidence)


def _severity_rank(severity: str) -> float:
    return {"high": 2.0, "medium": 1.0, "low": 0.0}.get(str(severity), 0.0)
