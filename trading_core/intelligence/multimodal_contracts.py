from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MultimodalSignal:
    source: str
    modality: str
    symbol: str
    label: str
    confidence: float
    enabled: bool = False
    reason: str = "multimodal_optional_disabled_by_default"

    @property
    def usable(self) -> bool:
        return bool(self.enabled) and self.confidence > 0.0
