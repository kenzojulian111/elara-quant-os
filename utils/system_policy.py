from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from config.settings import Config

CRITICAL_DEPENDENCY_STATES = ("down", "failed", "offline", "unavailable")
DEGRADED_DEPENDENCY_STATES = ("degraded", "slow", "stale", "partial")


@dataclass(frozen=True)
class SubsystemPolicy:
    name: str
    tier: str
    feature_flag: str = ""


@dataclass(frozen=True)
class DependencyPolicy:
    canonical_name: str
    tier: str
    scope: str
    on_down: str
    on_degraded: str = "degraded"
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class DependencyImpactObservation:
    dependency: str
    state: str
    tier: str
    scope: str
    effect: str
    reason: str = ""


_SUBSYSTEM_POLICIES = {
    "voice": SubsystemPolicy(name="voice", tier="experimental", feature_flag="ENABLE_VOICE"),
    "stream": SubsystemPolicy(name="stream", tier="experimental", feature_flag="ENABLE_STREAM"),
    "presence": SubsystemPolicy(name="presence", tier="experimental", feature_flag="PRESENCE_ENABLED"),
    "mqtt": SubsystemPolicy(name="mqtt", tier="experimental"),
    "device_control": SubsystemPolicy(name="device_control", tier="experimental"),
    "knowledge": SubsystemPolicy(name="knowledge", tier="experimental", feature_flag="ENABLE_KNOWLEDGE_SKILL"),
    "markdown_registry": SubsystemPolicy(
        name="markdown_registry",
        tier="experimental",
        feature_flag="LLM_MARKDOWN_SKILLS_ENABLED",
    ),
}

_DEPENDENCY_POLICIES = (
    DependencyPolicy(
        canonical_name="exchange",
        tier="core",
        scope="market_access",
        on_down="safe",
        on_degraded="degraded",
    ),
    DependencyPolicy(
        canonical_name="redis",
        tier="support",
        scope="event_bus",
        on_down="degraded",
        on_degraded="degraded",
    ),
    DependencyPolicy(
        canonical_name="db",
        tier="support",
        scope="audit_persistence",
        on_down="safe",
        on_degraded="degraded",
        aliases=("database", "postgres", "postgresql"),
    ),
    DependencyPolicy(
        canonical_name="trader_status",
        tier="support",
        scope="trader_link",
        on_down="degraded",
        on_degraded="degraded",
    ),
    DependencyPolicy(
        canonical_name="trader_setup",
        tier="support",
        scope="trader_setup",
        on_down="safe",
        on_degraded="degraded",
    ),
    DependencyPolicy(
        canonical_name="voice",
        tier="experimental",
        scope="operator_voice",
        on_down="full",
        on_degraded="full",
    ),
    DependencyPolicy(
        canonical_name="stream",
        tier="experimental",
        scope="operator_stream",
        on_down="full",
        on_degraded="full",
    ),
    DependencyPolicy(
        canonical_name="presence",
        tier="experimental",
        scope="operator_presence",
        on_down="full",
        on_degraded="full",
    ),
    DependencyPolicy(
        canonical_name="mqtt",
        tier="experimental",
        scope="device_control",
        on_down="full",
        on_degraded="full",
    ),
)

_DEPENDENCY_POLICY_BY_NAME = {
    alias: policy
    for policy in _DEPENDENCY_POLICIES
    for alias in (policy.canonical_name, *policy.aliases)
}


def _normalize_name(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def _split_csv(value: Any) -> set[str]:
    return {_normalize_name(item) for item in str(value or "").split(",") if str(item).strip()}


def subsystem_policy(name: str) -> SubsystemPolicy | None:
    return _SUBSYSTEM_POLICIES.get(_normalize_name(name))


def subsystem_policies() -> tuple[SubsystemPolicy, ...]:
    return tuple(_SUBSYSTEM_POLICIES.values())


def experimental_runtime_allowlist(config: Any = None) -> set[str]:
    cfg = config or Config
    return _split_csv(getattr(cfg, "EXPERIMENTAL_RUNTIME_ALLOWLIST", ""))


def enabled_experimental_subsystems(config: Any = None) -> tuple[str, ...]:
    return tuple(
        sorted(
            policy.name
            for policy in _SUBSYSTEM_POLICIES.values()
            if policy.tier == "experimental" and subsystem_runtime_enabled(policy.name, config)
        )
    )


def subsystem_runtime_enabled(name: str, config: Any = None) -> bool:
    policy = subsystem_policy(name)
    if policy is None or policy.tier != "experimental":
        return True
    cfg = config or Config
    if policy.name not in experimental_runtime_allowlist(cfg):
        return False
    if policy.feature_flag:
        return bool(getattr(cfg, policy.feature_flag, False))
    return True


def subsystem_can_block_core(name: str) -> bool:
    policy = subsystem_policy(name)
    if policy is None:
        return True
    return policy.tier != "experimental"


def effective_required_components(names: Iterable[str] | None) -> tuple[str, ...]:
    out: list[str] = []
    seen = set()
    for raw_name in names or ():
        name = _normalize_name(raw_name)
        if not name or name in seen:
            continue
        seen.add(name)
        if subsystem_can_block_core(name):
            out.append(name)
    return tuple(out)


def dependency_policy(name: str) -> DependencyPolicy:
    normalized = _normalize_name(name)
    return _DEPENDENCY_POLICY_BY_NAME.get(
        normalized,
        DependencyPolicy(
            canonical_name=normalized or "unknown",
            tier="core",
            scope="unknown",
            on_down="safe",
            on_degraded="degraded",
        ),
    )


def resolve_dependency_impact(
    name: str,
    state: str,
    *,
    blocking_states: Iterable[str] | None = None,
    degraded_states: Iterable[str] | None = None,
) -> DependencyImpactObservation:
    normalized_state = _normalize_name(state)
    policy = dependency_policy(name)
    blocking = {_normalize_name(item) for item in (blocking_states or CRITICAL_DEPENDENCY_STATES)}
    degraded = {_normalize_name(item) for item in (degraded_states or DEGRADED_DEPENDENCY_STATES)}

    if not normalized_state or normalized_state == "up":
        return DependencyImpactObservation(
            dependency=policy.canonical_name,
            state=normalized_state or "up",
            tier=policy.tier,
            scope=policy.scope,
            effect="full",
        )

    if normalized_state in blocking:
        effect = policy.on_down
        if effect == "full" and policy.tier == "experimental":
            reason = f"experimental_dependency_isolated={policy.canonical_name}:{normalized_state}:{policy.scope}"
        else:
            reason = f"{policy.tier}_dependency_down={policy.canonical_name}:{normalized_state}:{policy.scope}"
        return DependencyImpactObservation(
            dependency=policy.canonical_name,
            state=normalized_state,
            tier=policy.tier,
            scope=policy.scope,
            effect=effect,
            reason=reason,
        )

    if normalized_state in degraded:
        effect = policy.on_degraded
        if effect == "full" and policy.tier == "experimental":
            reason = f"experimental_dependency_isolated={policy.canonical_name}:{normalized_state}:{policy.scope}"
        else:
            reason = f"{policy.tier}_dependency_degraded={policy.canonical_name}:{normalized_state}:{policy.scope}"
        return DependencyImpactObservation(
            dependency=policy.canonical_name,
            state=normalized_state,
            tier=policy.tier,
            scope=policy.scope,
            effect=effect,
            reason=reason,
        )

    return DependencyImpactObservation(
        dependency=policy.canonical_name,
        state=normalized_state,
        tier=policy.tier,
        scope=policy.scope,
        effect=policy.on_degraded,
        reason=f"unknown_dependency_state={policy.canonical_name}:{normalized_state}:{policy.scope}",
    )

