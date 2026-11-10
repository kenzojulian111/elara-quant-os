from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Dict, List

from trading_core.data_contracts import StrategyPerformance, StrategyProfile


def _write_json_atomic(path: str, payload: dict) -> None:
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))
    os.replace(tmp_path, path)


@dataclass
class StrategyLibrary:
    profiles: Dict[str, StrategyProfile] = field(default_factory=dict)
    performance: Dict[str, StrategyPerformance] = field(default_factory=dict)
    metadata: Dict[str, str] = field(default_factory=dict)

    def register_profile(self, profile: StrategyProfile) -> None:
        self.profiles[profile.strategy_id] = profile

    def update_performance(self, perf: StrategyPerformance) -> None:
        self.performance[perf.strategy_id] = perf

    def save(self, path: str) -> None:
        payload = {
            "profiles": {k: _serialize_profile(v) for k, v in self.profiles.items()},
            "performance": {k: asdict(v) for k, v in self.performance.items()},
            "metadata": self.metadata,
        }
        _write_json_atomic(path, payload)

    @classmethod
    def load(cls, path: str) -> "StrategyLibrary":
        if not path or not os.path.exists(path):
            return cls()
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        lib = cls()
        for key, value in (raw.get("profiles") or {}).items():
            lib.profiles[key] = StrategyProfile(**_normalize_profile(value))
        for key, value in (raw.get("performance") or {}).items():
            lib.performance[key] = StrategyPerformance(**value)
        lib.metadata = {k: str(v) for k, v in (raw.get("metadata") or {}).items()}
        return lib

    def list_profiles(self) -> List[StrategyProfile]:
        return list(self.profiles.values())


def _serialize_profile(profile: StrategyProfile) -> dict:
    payload = asdict(profile)
    payload["tags"] = sorted({str(tag) for tag in (payload.get("tags") or [])})
    return payload


def _normalize_profile(payload: dict) -> dict:
    normalized = dict(payload or {})
    normalized["tags"] = set(payload.get("tags") or [])
    return normalized

