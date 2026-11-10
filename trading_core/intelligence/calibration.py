from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

from trading_core.intelligence.confidence import ConfidencePropagation


@dataclass(frozen=True)
class CalibrationObservation:
    predicted_probability: float
    outcome: float
    weight: float = 1.0
    label: str = ""


@dataclass(frozen=True)
class UncertaintyCalibration:
    raw_uncertainty: float
    calibrated_uncertainty: float
    calibration_error: float
    runtime_penalty: float
    conflict_penalty: float
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Mapping[str, object]:
        return {
            "raw_uncertainty": round(float(self.raw_uncertainty), 6),
            "calibrated_uncertainty": round(float(self.calibrated_uncertainty), 6),
            "calibration_error": round(float(self.calibration_error), 6),
            "runtime_penalty": round(float(self.runtime_penalty), 6),
            "conflict_penalty": round(float(self.conflict_penalty), 6),
            "reasons": tuple(self.reasons),
        }


@dataclass(frozen=True)
class UncertaintyCalibrationConfig:
    calibration_error_weight: float = 0.35
    runtime_penalty_degraded: float = 0.08
    runtime_penalty_safe: float = 0.18
    conflict_weight: float = 0.30
    propagation_uncertainty_weight: float = 0.45
    floor: float = 0.03


class AdaptiveUncertaintyCalibrator:
    def __init__(
        self,
        observations: Iterable[CalibrationObservation] = (),
        config: UncertaintyCalibrationConfig | None = None,
    ):
        self.observations = tuple(observations)
        self.config = config or UncertaintyCalibrationConfig()

    def calibrate(
        self,
        *,
        probability: float,
        raw_uncertainty: float,
        propagation: ConfidencePropagation,
        runtime_mode: str = "",
    ) -> UncertaintyCalibration:
        calibration_error = self._calibration_error(probability)
        runtime_penalty = self._runtime_penalty(runtime_mode)
        conflict_penalty = _clamp(float(propagation.conflict) * float(self.config.conflict_weight))
        propagation_penalty = _clamp(float(propagation.uncertainty) * float(self.config.propagation_uncertainty_weight))
        calibrated = _clamp(
            max(float(self.config.floor), float(raw_uncertainty))
            + calibration_error * float(self.config.calibration_error_weight)
            + runtime_penalty
            + conflict_penalty
            + propagation_penalty
        )
        reasons = (
            f"calibration_error={calibration_error:.4f}",
            f"runtime_penalty={runtime_penalty:.4f}",
            f"conflict_penalty={conflict_penalty:.4f}",
            f"propagation_penalty={propagation_penalty:.4f}",
            f"calibrated_uncertainty={calibrated:.4f}",
        )
        return UncertaintyCalibration(
            raw_uncertainty=_clamp(raw_uncertainty),
            calibrated_uncertainty=calibrated,
            calibration_error=calibration_error,
            runtime_penalty=runtime_penalty,
            conflict_penalty=conflict_penalty,
            reasons=reasons,
        )

    def _calibration_error(self, probability: float) -> float:
        if not self.observations:
            return 0.0
        total_weight = 0.0
        total_error = 0.0
        predicted = _clamp(probability)
        for observation in self.observations:
            weight = max(0.0, float(observation.weight))
            if weight <= 0:
                continue
            total_error += abs(predicted - _clamp(observation.outcome)) * weight
            total_weight += weight
        if total_weight <= 0:
            return 0.0
        return _clamp(total_error / total_weight)

    def _runtime_penalty(self, runtime_mode: str) -> float:
        mode = str(runtime_mode or "").strip().lower()
        if mode == "safe":
            return _clamp(float(self.config.runtime_penalty_safe))
        if mode == "degraded":
            return _clamp(float(self.config.runtime_penalty_degraded))
        return 0.0


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
