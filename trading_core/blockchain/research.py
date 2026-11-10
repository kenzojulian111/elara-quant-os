from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProtocolResearchFinding:
    protocol: str
    claim: str
    confidence: float
    verified_sources: tuple[str, ...] = field(default_factory=tuple)
    execution_authority: bool = False

    @property
    def verified(self) -> bool:
        return len(self.verified_sources) >= 2 and self.confidence >= 0.60


class ProtocolResearchLayer:
    def assess(
        self,
        *,
        protocol: str,
        claim: str,
        confidence: float,
        sources: tuple[str, ...],
    ) -> ProtocolResearchFinding:
        normalized_sources = tuple(sorted({str(source).strip() for source in sources if str(source).strip()}))
        return ProtocolResearchFinding(
            protocol=str(protocol or "unknown"),
            claim=str(claim or ""),
            confidence=max(0.0, min(1.0, float(confidence))),
            verified_sources=normalized_sources,
            execution_authority=False,
        )

    @staticmethod
    def can_execute(_: ProtocolResearchFinding) -> bool:
        return False
