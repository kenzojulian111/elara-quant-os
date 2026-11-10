from __future__ import annotations

import os
from collections.abc import Mapping


CORE_DEFAULTS: dict[str, str] = {
    "APP_ROLE": "core",
    "ENABLE_TRADING_SKILL": "1",
    "ENABLE_TRADING_ORCHESTRATOR": "0",
}

TRADER_DEFAULTS: dict[str, str] = {
    "APP_ROLE": "trader",
    "ENABLE_TRADING_SKILL": "0",
    "ENABLE_TRADING_ORCHESTRATOR": "0",
}

ROLE_DEFAULTS: dict[str, Mapping[str, str]] = {
    "core": CORE_DEFAULTS,
    "trader": TRADER_DEFAULTS,
}


def apply_role_defaults(role: str) -> None:
    """Apply conservative environment defaults for a runtime role."""
    normalized = str(role or "").strip().lower()
    try:
        defaults = ROLE_DEFAULTS[normalized]
    except KeyError as exc:
        known = ", ".join(sorted(ROLE_DEFAULTS))
        raise ValueError(f"Unknown runtime role {role!r}; expected one of: {known}") from exc

    for key, value in defaults.items():
        os.environ.setdefault(key, value)
