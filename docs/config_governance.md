# Config Governance Architecture

Elara treats configuration as a runtime contract, not as a dumping ground for flags.

The loader still supports the legacy `.env.example` -> `.env` -> process environment path, but the root example is now only a small bootstrap surface. Domain-specific defaults live in committed `config/*.env.example` files and optional local `config/*.env` overrides, so active configuration can be validated, redacted, hashed, and audited.

`pydantic-settings` is declared as a runtime dependency. The active loader keeps a dependency-tolerant dataclass registry so local bootstrap still works before dependencies are installed, and `config/pydantic_settings_schema.py` adds stricter Pydantic validation automatically when the package is available.

## Source Layers

Configuration is merged in this order:

1. `.env.example`
2. `config/core.env`
3. `config/runtime.env`
4. `config/trading.env`
5. `config/governance.env`
6. `config/observability.env`
7. `config/llm.env`
8. `config/blockchain.env`
9. `config/experimental.env`
10. `.env`
11. process environment

Actual `config/*.env` files are ignored by git. The committed `config/*.env.example` files are the bounded domain templates; keep new domain-specific keys there instead of expanding the root `.env.example`.

## Registry Domains

`Config.config_registry()` exposes typed domain objects:

- `core`
- `runtime`
- `trading`
- `governance`
- `observability`
- `llm`
- `blockchain`
- `experimental`
- `secrets`

Subsystems should depend on these typed objects or the stable grouped config view instead of reading arbitrary environment variables directly.

## Validation

`Config.config_governance_report()` returns:

- validation errors
- warnings
- unknown project config keys
- deprecated project config keys

Strict modes are separated so local development can warn without blocking, while production can fail fast:

```ini
CONFIG_STRICT_VALIDATION=1
CONFIG_STRICT_UNKNOWN_KEYS=1
CONFIG_STRICT_DEPRECATED_KEYS=1
```

Current hard dependency validation still rejects unsafe transport combinations such as Redis-required mode with file IPC fallback enabled.

## Deprecation And Unknown Keys

Deprecated keys are tracked with metadata in `config/governance.py`:

- deprecated since
- replacement key
- removal version
- description

Unknown keys are detected from project-owned env files. This catches typo-class failures like `TRDAING_IPC_ENABLED` before they become silent behavior drift.

## Snapshots

Every runtime can expose:

- `Config.deterministic_config_snapshot()`
- `Config.config_hash()`
- `Config.config_registry_snapshot()`
- `Config.config_registry_hash()`
- `Config.config_feature_map()`

When enabled, boot appends a redacted audit event to `runtime/config_snapshots.jsonl`:

```ini
CONFIG_SNAPSHOT_AUDIT_ENABLED=1
CONFIG_SNAPSHOT_AUDIT_PATH=runtime/config_snapshots.jsonl
```

The snapshot stores hashes, source layers, feature maps, and validation output without leaking secret values.

## Secrets

The registry records whether secret-bearing keys are configured or placeholders, but it does not expose the raw values. Secrets should move toward a deployment secret manager over time; `.env` remains acceptable for local development and small deployments.

## Experimental Isolation

Experimental features are isolated into an explicit domain and disabled by default. `RUNTIME_COMPLEXITY_MAX_EXPERIMENTAL` prevents accidental activation of too many experimental subsystems at once.

## Migration Rule

Do not add new config by dropping another flag into the root `.env.example` only.

New config should declare:

- domain ownership
- default value
- validation behavior
- whether it is secret-bearing
- whether it is experimental
- deprecation metadata if replacing an older key
