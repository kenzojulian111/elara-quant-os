# Elara Unified (Core + Trader)

Elara is an AI-native trading decision platform with an assistant interface. This repository hosts the assistant runtime, the trader daemon, the shared decision engine, and the first party skills and interfaces around that core. It is designed to run as a single node for development and as split core and trader services in production.

This README is intentionally exhaustive. It covers local dev, architecture, configuration, interfaces, skills, and trading and screener internals. Where exact behavior depends on configuration or runtime data, the README calls that out explicitly.

## Key Features

- Unified core and trader runtimes with explicit role separation
- Skill based trading assistant architecture with experimental surfaces kept opt-in
- Trading engine with risk gating, event ledger, and backtest and simulation tooling
- Crypto and stock screeners with multi layer scoring and risk adjusted filters
- Redis event bus with file fallback for commands and heartbeats
- Web and Telegram operator interfaces, with voice, stream, presence, and IoT kept optional
- Strong runtime guardrails and fail fast safety checks
- Extensive test suite and quality gate scripts

## Table of Contents

1. Project Goals and Scope
2. Tech Stack
3. System Roles and Entry Points
4. Architecture Overview
5. Directory Structure
6. Core Runtime Deep Dive
7. Trader Runtime Deep Dive
8. Trading Core Library
9. Screeners Deep Dive
10. Skills System
11. Interfaces and Integrations
12. Data, State, and Storage
13. Configuration System
14. Environment Variables
15. Local Development
16. Docker Compose
17. Scripts and Operational Tools
18. Testing and Quality Gates
19. Deployment Notes
20. Troubleshooting
21. File Inventory
22. License

## Project Goals and Scope

Elara is not a generic assistant with trading bolted on. It is a coordinated trading decision system:

- Elara Core is the assistant interface layer that exposes web and messaging surfaces and executes skills.
- Elara Trader is the execution runtime focused on market connectivity, order flow, and safeguards.
- Trading Core is the decision engine with signals, strategies, risk, simulation, and orchestration.
- Skills provide capabilities across trading, knowledge, automation, and system operations.

This repository is the primary codebase. Legacy copies still exist under `ElaraCore/` and `ElaraTrader/` for reference, but the active entry points are under `apps/` and `main.py`.

## Tech Stack

- Language: Python 3.11 for Docker, Python 3.12 for dev tooling and mypy config
- Web runtime: Flask, Flask SocketIO
- API framework: Flask for core, FastAPI for webhook service
- Data science: pandas, numpy, scipy, scikit-learn
- Trading data: ccxt, yfinance, OpenBB adapter
- Storage: PostgreSQL, Redis
- Task scheduling: APScheduler
- Messaging: Telegram bot integrations
- Security: Flask JWT Extended, Flask Limiter, Flask WTF, flask talisman
- LLM providers: OpenAI, Groq, Gemini
- Observability: structured JSON logs, Prometheus metrics, Grafana dashboards, Loki logs, optional OpenTelemetry and Tempo traces
- Packaging: requirements.in and requirements-dev.txt

## System Roles and Entry Points

Use these entry points to start the system:

| Role | Entry Point | Description |
| --- | --- | --- |
| Unified | `python main.py` | Runs core role by default in unified repo |
| Core | `python apps/elara_core/main.py` | Starts core assistant runtime only |
| Trader | `python apps/elara_trader/main.py` | Starts trading daemon only |
| Webhook | `python apps/webhook_fastapi/main.py` | Starts FastAPI webhook service |

Observability setup lives in [docs/observability_q1.md](docs/observability_q1.md).

Role defaults are set via environment variables inside the entry points:

- Core sets `APP_ROLE=core`, `ENABLE_TRADING_SKILL=1`
- Trader sets `APP_ROLE=trader`, `ENABLE_TRADING_SKILL=0`

## Architecture Overview

The system is split into core, trader, and shared trading core. The assistant talks to the trader via Redis event bus or file fallback when configured. Skills encapsulate features and can be enabled or disabled at runtime. The platform identity is frozen in `CORE_IDENTITY.md`: trading decision paths are core, while voice, presence, IoT, and markdown routing stay opt-in.

The architectural doctrine for roadmap execution is captured in [docs/architecture_doctrine.md](docs/architecture_doctrine.md). It defines the responsibility boundaries, governance posture, runtime safety rules, decision abstraction, explainable decision flow, and layered orchestration contract.

Year 1 Q1-Q3 implementation status is tracked in [docs/year1_q1_q3_execution.md](docs/year1_q1_q3_execution.md).

Year 1 Q4 through Year 2 Q4 implementation status is tracked in [docs/year1_q4_year2_q4_execution.md](docs/year1_q4_year2_q4_execution.md).

The focused Year 1 completion gate is tracked in [docs/year1_completion.md](docs/year1_completion.md).

```mermaid
graph TD
  User[User or Operator] --> Web[Web UI / Portal]
  User --> Telegram[Telegram Bot]
  User --> Voice[Voice Interface]

  Web --> Core[Elara Core]
  Telegram --> Core
  Voice --> Core

  Core --> Skills[Skill Manager]
  Skills --> TradingSkill[TraderGatewaySkill]
  Skills --> Screeners[Stock/Crypto Screeners]
  Skills --> Knowledge[Knowledge + Internet + LLM Skills]

  TradingSkill --> Redis[Redis Event Bus]
  Core --> Redis
  Trader[Elara Trader] --> Redis
  Trader --> Exchange[Exchange APIs]

  Core --> DB[(PostgreSQL)]
  Trader --> DB
  Core --> State[State Manager]
  State --> Redis
  State --> Files[Runtime JSON fallback]
```

## Directory Structure

Top level directories and their roles:

```
alembic/            DB migrations for SQLAlchemy
apps/               Runtime entrypoints (core, trader, webhook_fastapi)
config/             Global settings, device config, env handling
core/               Core runtime bootstrap, NLP, skill manager, state manager
database/           DB models, repositories, schema, dataset
deploy/             Service files, cron, cloudflared config
docs/               Architecture and operational docs
documents/          Extended runbooks and training docs
ElaraCore/          Legacy core runtime (reference)
ElaraTrader/        Legacy trader runtime (reference)
runtime/            Runtime files, logs, state, cached assets
scripts/            Ops scripts and utilities
skills/             Skill implementations, trading daemon
static/             CSS assets
templates/          Flask HTML templates
tests/              Unit and integration tests
trading_core/       Core trading library (signals, strategy, risk, simulation)
utils/              Shared utilities (redis, secret provider, logging)
```

## Core Runtime Deep Dive

Core bootstrap is in `core/app_bootstrap.py`. It is responsible for:

- Validating runtime configuration and safety guardrails
- Initializing state manager, database manager, life manager
- Loading LLM clients and NLP engine
- Loading device config and MQTT service
- Registering skills
- Starting interfaces (web, telegram, scheduler, voice, stream)
- Publishing core heartbeat
- Running watchdogs for services

Key behaviors:

- `_validate_runtime_config` enforces secrets, DB backend, Redis requirements, LLM key availability, and reverse proxy guards.
- `_register_skills` registers built in skills and optional skills based on env toggles.
- `_start_interfaces` creates Flask app, SocketIO, and optional services.
- `_start_core_heartbeat_publisher` writes heartbeat to Redis and file fallback.

Core uses `ElaraSkill` as the orchestrator, which wires NLP to skill execution.

## Trader Runtime Deep Dive

Trader bootstrap lives in `core/trader_bootstrap.py`. It is intentionally thin and hands off to:

- `skills.advanced.trading.daemon_runner` for the trading loop

The trader runs separately in Docker Compose and can run independently in local dev. It depends on Redis and PostgreSQL in production mode, and it enforces core heartbeat if `TRADER_REQUIRE_CORE_HEARTBEAT=1`.

## Trading Core Library

The `trading_core/` directory is the shared trading engine.

Important modules:

- `trading_core/data_contracts.py` defines core data types like `MarketState`, `IndicatorSnapshot`, `StrategyDecision`, and `RiskDecision`.
- `trading_core/indicators/basic.py` is a streaming indicator calculator with EMA, RSI, ATR, MACD, ADX, and Bollinger Bands.
- `trading_core/signals/technical.py` builds a higher order technical signal from indicators.
- `trading_core/strategy/` holds strategy implementations and a factory.
- `trading_core/risk/` enforces max risk per trade and drawdown limits.
- `trading_core/simulation/` runs backtests with a replay engine.
- `trading_core/orchestration/attention_engine.py` decides how much of the portfolio deserves full attention under the current market and runtime context.
- `trading_core/orchestration/objective_engine.py` makes the active trading objective explicit: `preserve_capital`, `balanced`, or `maximize_profit`.
- `trading_core/orchestration/temporal_reasoner.py` keeps short horizon memory for regime persistence and recent strategy outcomes, then converts that into temporal bias or veto.
- `trading_core/orchestration/decision_core.py` turns a raw strategy signal into a `DecisionEnvelope` with trace, candidate, risk result, and execution plan.
- `trading_core/orchestration/decision_reasoner.py` synthesizes a final `ReasonedDecision` from strategy, portfolio, objective, temporal, risk, runtime, and governance inputs.
- `trading_core/orchestration/` runs shadow and live pipelines, routes every trading action through a centralized decision core, applies runtime and governance checks, then respects the final reasoned decision before execution.
- `trading_core/portfolio/` now acts as a meta-decision layer: it classifies market regime, self-reviews strategy health, suppresses blocked strategies, and downweights degraded ones before execution.
- `trading_core/evaluation/` computes performance metrics like Sharpe and max drawdown.

Decision flow precedence:

1. Portfolio brain classifies regime and produces a portfolio decision plan with selected, blocked, and suppressed strategies.
2. Attention engine decides whether Elara should run a full scan or a reduced focus set, and which strategies deserve evaluation first.
3. Objective engine decides what the system is optimizing right now: preserve capital, stay balanced, or push for profit.
4. Temporal reasoner adds memory of persistent market regime and recent strategy loss streaks before a new entry is trusted.
5. Strategy proposes an action and reasons.
6. Decision core builds a `DecisionEnvelope` with signal trace, entry confidence gates, candidate sizing, and risk outcome.
7. Runtime mode is assessed as `full`, `degraded`, or `safe` from dependency and health state.
8. Decision governor applies operator state, runtime mode, market regime, and safety gates.
9. Decision reasoner fuses strategy, portfolio, objective, temporal, risk, runtime, and governance into one `ReasonedDecision`.
10. Execution is allowed only when that final reasoned decision still approves the action.

The important architectural point is that Elara is not only a chain of filters. The pipeline still exists, but it now converges into a single decision object:

- `DecisionEnvelope` is the shared transport for the whole trading decision path.
- `ReasonedDecision` is the final, explainable verdict with action, confidence, constraints, regime, risk score, and component breakdown.
- `ObjectiveProfile` and `StrategyObjectiveBias` make Elara's current mission explicit, so a valid signal can still be rejected when it violates the active objective.
- `TemporalContext` and `StrategyTemporalBias` give the pipeline a short operational memory so it can treat persistent range and repeated losses differently from isolated snapshots.
- The shadow pipeline will not execute a trade if the final reasoned decision vetoes it, even when earlier stages produced an execution plan.

Governor operating modes:

- `green` allows normal operation
- `yellow` blocks new entries but allows observation and position management
- `red` freezes new entries and keeps exit actions available for safety

Strategies:

- Trend Following uses EMA stack, ADX, MACD, and Bollinger width to confirm trend entries and exits.
- Mean Reversion uses RSI, Bollinger bands, and ADX for range bound entries.

## Screeners Deep Dive

Elara includes two major screeners:

- `skills/StockScreenerSkill.py`
- `skills/CryptoScreenerSkill.py`

Both screeners:

- Pull OHLCV from OpenBB, ccxt, or yfinance
- Compute trend, volatility, volume, and risk adjusted metrics
- Use a multi factor score to filter candidates
- Estimate a win probability and expected value
- Apply Kelly scaling to position sizing

Key metrics used in scoring:

- Trend stack and breakout detection
- ATR percent for tradable volatility
- Relative strength versus benchmark
- Sharpe, Sortino, trend efficiency, max drawdown
- Hurst exponent and multi window trend slope
- CVaR, Ulcer Index, skew, kurtosis
- Volume spike detection and liquidity checks

Outputs include:

- Entry, stop, TP1, TP2
- RR, win probability, expected value
- Position sizing suggestion and conviction scaling
- Tags that explain the score drivers

Important note:

Screeners are decision support. They do not guarantee profitability. Always validate via backtests and paper trading before live deployment.

## Skills System

Skills are registered in `core/app_bootstrap.py`. The core skill list includes:

- SimpleChatSkill
- LifeSkill
- GoalEngineSkill
- SecuritySkill
- DeepFokusSkill
- WeatherSkill
- CalendarSkill
- SpotifySkill
- DeviceControlSkill
- TraderGatewaySkill
- CryptoScreenerSkill
- StockScreenerSkill
- LLMSkill
- HomeAdguardSkill
- SystemHealthSkill
- KnowledgeSkill
- InternetSkill
- ExecutiveBriefingSkill
- SelfOpsSkill

Skill execution flow:

1. User message is processed by `ElaraNLP`
2. Intent is routed via `SkillManager`
3. Skill returns response and optional tool execution

Markdown skills:

- Markdown skill registry is opt-in via `LLM_MARKDOWN_SKILLS_ENABLED=1`
- Skills are loaded from `LLM_MARKDOWN_SKILLS_DIRS` and filtered by profile and trust
- Route is based on content search and feedback weighting

## Interfaces and Integrations

Core can run multiple interfaces:

- Web UI with templates under `templates/` and CSS under `static/`
- Telegram service
- Voice service
- Stream service
- Scheduler service
- Presence service
- Backup service
- Health service

## Data, State, and Storage

State manager:

- Supports Redis, file, memory backends
- Uses encryption when enabled
- File fallback in runtime directory when configured

Database:

- SQLAlchemy models under `database/`
- Alembic migrations under `alembic/`
- `DB_ALLOW_SQLITE=0` in production to force PostgreSQL

Redis:

- Event bus for trade signals and heartbeats
- Supports Redis based state backend

Runtime files:

- `runtime/` stores caches, logs, state, and exported artifacts

## Configuration System

Configuration is centralized in `config/settings.py`:

- Supports profile based defaults via `CONFIG_PROFILE` for `development`, `paper`, `backtest`, and `live`
- Exposes a smaller core operator surface via `Config.core_config()` and keeps the rest in `Config.advanced_config()`
- Reads `.env.example` as base
- Optionally layers domain files from `config/*.env`
- Merges `.env` overrides
- Uses process environment variables as highest priority
- Coerces types for booleans and numbers
- Resolves paths relative to repo root
- Exposes typed domain governance through `Config.config_registry()` and `Config.config_registry_snapshot()`
- Adds optional stricter Pydantic Settings validation when `pydantic-settings` is installed
- Emits deterministic redacted snapshots and hashes through `Config.deterministic_config_snapshot()` / `Config.config_hash()`

Important behaviors:

- Production environment guards enforce Redis and DB server usage
- Placeholders are rejected for secrets
- Reverse proxy guard is required in production by default
- Unknown and deprecated project config keys are reported at startup
- Strict governance modes can fail fast on invalid domain contracts

## Environment Variables

There are many environment variables. Use `.env.example` as the source of truth.

Recommended config workflow:

- Run `.\scripts\create_dev_env.ps1` for a lightweight local `.env`
- Use `.env.example` when you need the full exhaustive surface
- Keep production secrets in `.env` or your deployment secret manager, never in source control
- Transport defaults are now explicit by profile: `development` and `backtest` prefer file transport, while `paper` and `live` prefer Redis and disable file fallback by default
- Code can now consume a typed grouped config view via `Config.config_schema()` / `Config.grouped_config()` for `trading`, `infra`, `security`, and `llm`
- Stable app-facing config surface is now exposed as `Config.grouped_config_v1()` with explicit contract metadata from `Config.grouped_config_contract()`
- Domain governance is documented in `docs/config_governance.md`; use `config/*.env.example` as the migration map when splitting the root env surface
- Anything outside this grouped `v1` surface should be treated as advanced/internal until deliberately promoted

Complexity governance:

- `ComplexityGovernanceChecker` tracks config entropy, docs coverage, protocol contracts, replay/event foundations, and honest frontier gaps
- Run `python scripts/check_complexity_governance.py --fail-on-critical` before promoting roadmap work
- Details live in `docs/complexity_governance.md`

Minimal required values for local dev:

| Variable | Description |
| --- | --- |
| `SECRET_KEY` | Core secret for sessions |
| `JWT_SECRET_KEY` | JWT signing secret |
| `POSTGRES_PASSWORD` | PostgreSQL password |
| `REDIS_PASSWORD` | Redis password |
| `DATABASE_URL` | Database connection string |
| `REDIS_URL` | Redis connection string |

LLM keys are optional in dev when `LLM_ALLOW_NO_API_KEY_IN_DEV=1`, but required for full LLM functionality.

### `.env.example` Excerpt

```ini
# Core security
SECRET_KEY=replace-with-strong-secret
JWT_SECRET_KEY=replace-with-strong-jwt-secret
ALLOW_DEV_BACKDOOR=0
FAIL_FAST_STARTUP=0
FLASK_ENV=development
DEBUG=0

# Config governance
CONFIG_GOVERNANCE_ENABLED=1
CONFIG_DOMAIN_FILES_ENABLED=1
CONFIG_DOMAIN_FILES=config/core.env,config/runtime.env,config/trading.env,config/governance.env,config/observability.env,config/llm.env,config/blockchain.env,config/experimental.env
CONFIG_STRICT_VALIDATION=0
CONFIG_STRICT_UNKNOWN_KEYS=0
CONFIG_STRICT_DEPRECATED_KEYS=0
CONFIG_SNAPSHOT_AUDIT_ENABLED=1
CONFIG_SNAPSHOT_AUDIT_PATH=runtime/config_snapshots.jsonl

# Runtime role toggles
ENABLE_TRADING_SKILL=1
ENABLE_KNOWLEDGE_SKILL=0
ENABLE_TRADING_ORCHESTRATOR=0
REQUIRED_COMPONENTS=web,scheduler
ENABLE_VOICE=0
ENABLE_STREAM=0
EXPERIMENTAL_AUTO_RESEARCH_ENABLED=0
EXPERIMENTAL_MULTIMODAL_ENABLED=0
WEB_LEGACY_DASHBOARD_ENABLED=0
WEB_TG_DASHBOARD_ENABLED=0
SCREENER_VISUAL_CACHE_TTL_SEC=6
ADMIN_BOOTSTRAP_EMAIL=
ADMIN_BOOTSTRAP_PASSWORD=
ADMIN_BOOTSTRAP_NAME=Elara Admin
ADMIN_BOOTSTRAP_ROLE=admin

# Database / cache
POSTGRES_USER=elara
POSTGRES_DB=elara
POSTGRES_PASSWORD=replace-with-strong-db-password
DATABASE_URL=postgresql://elara:replace-with-strong-db-password@localhost:5432/elara
DB_ALLOW_SQLITE=0
DB_REQUIRE_SERVER_FOR_PROD=1
DB_REQUIRE_SERVER_FOR_LIVE_TRADING=1
DB_COMMIT_RETRY_MAX_ATTEMPTS=3
DB_COMMIT_RETRY_BACKOFF_SEC=0.2
DB_COMMIT_RETRY_MAX_BACKOFF_SEC=2.0
SQLITE_BUSY_TIMEOUT=5
COMMAND_LOG_MAX_TEXT_CHARS=4000
COMMAND_LOG_MAX_ROWS=200000
COMMAND_LOG_PRUNE_BATCH=1000
COMMAND_LOG_PRUNE_INTERVAL_SEC=60
DB_AUTO_CREATE_TABLES=1
DB_USE_ALEMBIC=0
DB_ALEMBIC_AUTO_UPGRADE=0
DB_ALEMBIC_CONFIG=alembic.ini
REDIS_PASSWORD=replace-with-strong-redis-password
REDIS_URL=redis://:replace-with-strong-redis-password@localhost:6379/0
REDIS_PREFIX=elara:
REDIS_CONNECT_RETRY_MAX_ATTEMPTS=3
REDIS_CONNECT_RETRY_BASE_SEC=0.15
REDIS_CONNECT_RETRY_MAX_SEC=2.0
REDIS_MAX_CONNECTIONS=64
REDIS_SOCKET_TIMEOUT_SEC=1.5
REDIS_SOCKET_CONNECT_TIMEOUT_SEC=1.5
REDIS_OP_RETRY_MAX_ATTEMPTS=2
REDIS_OP_RETRY_BACKOFF_SEC=0.1
REDIS_HEALTHCHECK_TTL_SEC=6
STATE_BACKEND=auto
STATE_REDIS_PREFIX=elara:
STATE_FILE_LOCK_TIMEOUT_SEC=5
STATE_ALLOW_MEMORY_FALLBACK=1
STATE_BACKEND_FAIL_FAST=0
REDIS_EVENT_BUS_REQUIRED=0
TRADING_FILE_IPC_FALLBACK_ENABLED=1
TRADING_STATUS_FILE_FALLBACK_ENABLED=1
CORE_HEARTBEAT_FILE_FALLBACK_ENABLED=1

# Pub/Sub bridge
TRADING_SIGNAL_CHANNEL=elara:events:trade_signal
TRADING_COMMAND_KEY=elara:events:trade_command_queue
TRADING_STATUS_KEY=elara:events:trader_status
CORE_HEARTBEAT_KEY=elara:events:core_heartbeat
CORE_HEARTBEAT_PATH=runtime/core_heartbeat.json
CORE_HEARTBEAT_INTERVAL_SEC=5
SCHEDULER_JOBSTORE_URL=
BACKUP_ENABLED=0
BACKUP_HOUR=2
BACKUP_MINUTE=15
BACKUP_RETENTION_DAYS=7
BACKUP_LOCAL_DIR=runtime/backups
BACKUP_GDRIVE_FOLDER_ID=
BACKUP_GDRIVE_SHARE_TYPE=none
BACKUP_GDRIVE_SHARE_ROLE=reader
BACKUP_GDRIVE_SHARE_EMAIL=
BACKUP_GDRIVE_SHARE_DOMAIN=
BACKUP_GDRIVE_SHARE_ALLOW_DISCOVERY=0
BACKUP_PG_DUMP_TIMEOUT_SEC=120
TRADER_REQUIRE_CORE_HEARTBEAT=0
TRADER_CORE_HEARTBEAT_STALE_SEC=30
TRADER_CORE_HEARTBEAT_CHECK_SEC=5
TRADER_CORE_HEARTBEAT_GRACE_SEC=120
CRITICAL_CONFIRM_ENABLED=1
CRITICAL_CONFIRM_TTL_SEC=120
CRITICAL_CONFIRM_TOKEN_BYTES=3
CRITICAL_CONFIRM_INTENTS=trading_stop,hedge_fund_stop,security_block_ip
CRITICAL_CONFIRM_TOOLS=flatten_all_positions
PROMPT_INJECTION_GUARD_ENABLED=1
LLM_TOOL_CALLING_ENABLED=1
LLM_TOOL_MAX_CALLS=4
LLM_TOOL_SUMMARY_ENABLED=1
LLM_AGENT_ORCHESTRATOR_ENABLED=1
LLM_AGENT_MAX_STEPS=3
LLM_AGENT_MAX_LOOPS=3
LLM_AGENT_MAX_TOTAL_TOOL_CALLS=8
LLM_AGENT_TOOL_ALLOWLIST=turn_on_device,turn_off_device,get_weather_forecast,cek_cuaca,adguard_enable,adguard_disable,adguard_status,adguard_block_domain,get_trading_status
LLM_AGENT_TOOL_DENYLIST=self_deploy,self_heal
LLM_AGENT_ALLOW_CRITICAL_TOOLS=0
LLM_HISTORY_TURNS=14
LLM_HISTORY_MAX_CHARS=7000
LLM_HISTORY_MAX_TOKENS=4000
LLM_HISTORY_ITEM_MAX_CHARS=900
LLM_HISTORY_TTL_SEC=21600
LLM_MARKDOWN_SKILLS_ENABLED=0
LLM_MARKDOWN_SKILLS_PROFILE=custom
LLM_MARKDOWN_SKILLS_INCLUDE=elara-trading-ops,systematic-debugging,verification-before-completion,python-fastapi-development,quant-analyst,risk-manager,risk-metrics-calculation,backtesting-frameworks,telegram-automation,webapp-testing,security-audit,api-security-best-practices
LLM_MARKDOWN_SKILLS_EXCLUDE=skill-creator,loki-mode,using-superpowers
LLM_MARKDOWN_POLICY_ENABLED=0
LLM_MARKDOWN_MIN_TRUST=restricted
LLM_MARKDOWN_DEFAULT_TRUST=standard
LLM_MARKDOWN_BLOCKED_SKILLS=
LLM_MARKDOWN_TRUST_OVERRIDES=
LLM_MARKDOWN_PROFILE_SKILL_RULES=
LLM_MARKDOWN_USER_PROFILE_OVERRIDES=
LLM_MARKDOWN_ROLE_PROFILE_OVERRIDES=
LLM_MARKDOWN_USER_MIN_TRUST_OVERRIDES=
LLM_MARKDOWN_ROLE_MIN_TRUST_OVERRIDES=
LLM_MARKDOWN_USER_BLOCKED_SKILLS_OVERRIDES=
LLM_MARKDOWN_ROLE_BLOCKED_SKILLS_OVERRIDES=
LLM_MARKDOWN_USER_ROLE_OVERRIDES=
LLM_MARKDOWN_PROMPT_GUARD_ENABLED=1
LLM_MARKDOWN_BLOCK_SUSPICIOUS_BODY=1
LLM_MARKDOWN_ROUTER_ENABLED=0
LLM_MARKDOWN_ROUTER_MIN_SCORE=70
LLM_MARKDOWN_ROUTER_TOP_K=8
LLM_MARKDOWN_FEEDBACK_ROUTER_ENABLED=1
LLM_MARKDOWN_FEEDBACK_ROUTER_MIN_SAMPLES=3
LLM_MARKDOWN_FEEDBACK_ROUTER_MAX_BOOST=45
LLM_MARKDOWN_FEEDBACK_ROUTER_MAX_PENALTY=60
LLM_MARKDOWN_FEEDBACK_MATCH_WEIGHT=0.35
LLM_MARKDOWN_SKILLS_DIRS=../my-skills
LLM_MARKDOWN_SKILLS_MAX_COUNT=1200
LLM_MARKDOWN_SKILLS_MAX_BODY_CHARS=6000
LLM_MARKDOWN_SKILLS_REFRESH_SEC=30
LLM_MARKDOWN_SKILLS_LIST_LIMIT=60
LLM_MARKDOWN_SKILLS_SEARCH_LIMIT=20
LLM_MARKDOWN_METRICS_RECENT_LIMIT=80
NLP_MAX_REGEX_INPUT_CHARS=4000
NLP_CACHE_ENABLED=1
NLP_CACHE_TTL_SEC=600
NLP_TOOL_CACHE_TTL_SEC=300
NLP_CACHE_MAX_COMMAND_CHARS=180
NLP_CACHE_MAX_ENTRIES_PER_USER=400
LIFE_TRADING_BLOCK_COMMAND=stop
LIFE_TRADING_RESUME_COMMAND=resume
TRADER_API_BASE_URL=http://127.0.0.1:8768
TRADER_API_TIMEOUT_SEC=2.0
TRADER_GATEWAY_DEDUP_TTL_SEC=60
TRADING_METRICS_EQUITY_HISTORY_LIMIT=50
TRADING_IPC_HOST=127.0.0.1
TRADING_IPC_PORT=8768
TRADING_IPC_ENABLED=1
# Optional IPC auth token (required for non-loopback requests)
TRADING_IPC_TOKEN=

# Life policy thresholds
LIFE_MIN_SLEEP_HOURS=5.0
LIFE_MAX_STRESS=7
LIFE_MIN_ENERGY=0
LIFE_MIN_FOCUS_MINUTES=120
LIFE_CONTEXT_MAX_AGE_SEC=86400

# Trading guardrails
DAILY_MAX_LOSS_PCT=10
MAX_DRAWDOWN_PCT=10
TRADING_FALLBACK_SIM_ENABLED=1
TRADING_MODE=paper
HEALTH_EXCHANGE_REQUIRED=0
TRADING_STRATEGY=swing
TRADING_SYMBOL=BTC/USDT
APP_ENV=development
ALLOW_LIVE_IN_DEV=0
SPREAD_MAX_PCT=1.0
MIN_NET_EDGE_MULTIPLE_FEE=2.0
DUST_MIN_NOTIONAL_USDT=5.0
EXCHANGE_MAX_TIME_DRIFT_MS=2000
EXCHANGE_ORDER_FETCH_RETRIES=3
EXCHANGE_ORDER_RETRY_BASE_SEC=1.0
EXCHANGE_ORDER_RETRY_MAX_SEC=8.0
EXCHANGE_RATE_LIMIT_MAX_CALLS=500
EXCHANGE_RATE_LIMIT_PERIOD_SEC=60
EXCHANGE_API_ERROR_THRESHOLD=3
EXCHANGE_API_PAUSE_SEC=3600
EXCHANGE_MAINTENANCE_PAUSE_SEC=600
EXCHANGE_WEIGHT_WARN_PCT=80
EXCHANGE_WEIGHT_HARD_PCT=95
EXCHANGE_WEIGHT_PAUSE_SEC=30
EXCHANGE_STP_MODE=
EXCHANGE_IP_WHITELIST_REQUIRED=1
EXCHANGE_ALLOWED_IPS=
EXCHANGE_CLIENT_ORDER_PREFIX=elara
ORDER_USE_LIMIT_CHASE=1
ORDER_CHASE_TIMEOUT_SEC=10
ORDER_CHASE_MAX_RETRIES=3
ORDER_CHASE_POLL_SEC=1.0
ORDER_CHASE_MARKET_FALLBACK=1
ORDER_CHASE_MIN_FILL_RATIO=0.98
EXCHANGE_SYMBOL_VALIDATION_REQUIRED=1
ORDER_SELL_ROUND_DOWN=1
EXCHANGE_CREDENTIAL_RELOAD_ENABLED=1
EXCHANGE_CREDENTIAL_RELOAD_CHECK_SEC=10
ORDER_MAX_SLIPPAGE_PCT=1.0
ORDERBOOK_DEPTH_LIMIT=20
WS_STALE_SEC=60
WS_SEQUENCE_CHECK_ENABLED=1
WS_SEQUENCE_MAX_JUMP=100
RUNTIME_CONFIG_REFRESH_SEC=20
OPEN_ORDER_SYNC_INTERVAL_SEC=60
OPEN_ORDER_SYNC_BATCH=200
RECONCILE_POSITIONS_INTERVAL_SEC=60
OPTIMISTIC_BALANCE_ENABLED=1
BALANCE_RECONCILE_INTERVAL_SEC=30
BALANCE_RECONCILE_TOLERANCE_ABS=5.0
BALANCE_RECONCILE_TOLERANCE_PCT=2.0
BALANCE_RECONCILE_GRACE_SEC=20
DELIST_WATCHDOG_ENABLED=1
DELIST_WATCHDOG_INTERVAL_SEC=900
DELIST_FORCE_EXIT_ENABLED=1
DELIST_STATUS_RISK_FLAGS=break,halt,suspend,close,closed,delist,delisting
DELIST_MONITORING_SYMBOLS=
PANIC_WITHDRAW_MONITOR_ENABLED=0
PANIC_WITHDRAW_MONITOR_INTERVAL_SEC=300
PANIC_WITHDRAW_VOLUME_DROP_PCT=60
PANIC_WITHDRAW_PRICE_DROP_PCT=18
PANIC_WITHDRAW_BENCHMARK_SYMBOL=BTC/USDT
PANIC_WITHDRAW_AUTO_STOP=1
PANIC_WITHDRAW_ALERT_COOLDOWN_SEC=300
PANIC_WITHDRAW_BASELINE_WINDOW=24
PANIC_WITHDRAW_BASELINE_MIN_POINTS=6
MEMORY_HARDENING_WARN_ON_SWAP=1
MEMORY_HARDENING_REQUIRE_MLOCK=0
MEMORY_HARDENING_TRY_MLOCKALL=1
MEMORY_HARDENING_LEVEL=warning
BACKTEST_PROCESS_WORKERS=0
MAX_ORDER_NOTIONAL_PCT=20
MAX_ORDER_NOTIONAL_USDT=5000
RSI_BUY_MIN=52
RSI_BUY_MAX=68
RSI_SELL_MIN=28
RSI_SELL_MAX=48
RSI_OVERBOUGHT=75
SWING_MOMENTUM_Z_POS_THRESHOLD=0.75
SWING_MOMENTUM_Z_NEG_THRESHOLD=-0.75
SWING_BB_BREAK_VOL_Z_THRESHOLD=0.8
SWING_VWAP_DIST_POS_THRESHOLD=0.2
SWING_VWAP_DIST_NEG_THRESHOLD=-0.8
SWING_REALIZED_VOL_MULTIPLIER=1.4
SWING_DOWNSIDE_VOL_RATIO_THRESHOLD=0.65
SCALP_RSI_BUY_BUFFER=4.0
SCALP_MOMENTUM_Z_POS_THRESHOLD=1.0
SCALP_MOMENTUM_Z_NEG_THRESHOLD=-1.0
SCALP_VWAP_DIST_POS_THRESHOLD=0.15
SCALP_VWAP_DIST_NEG_THRESHOLD=-0.7
SCALP_VOL_Z_EXPAND_THRESHOLD=1.0
NOTIFIER_TEXT_CHUNK_SIZE=3900
NOTIFIER_THROTTLE_WINDOW_SEC=60
NOTIFIER_THROTTLE_BURST=5
NOTIFIER_THROTTLE_CRITICAL_BYPASS=1
NOTIFIER_THROTTLE_CRITICAL_KEYWORDS=panic,critical,liquidation,drawdown,risk,order failed,rejected,reconcile,withdraw
SENTIMENT_MODE=heuristic
SENTIMENT_FETCH_TIMEOUT_SEC=4.0
SENTIMENT_FETCH_CONCURRENCY=4
SENTIMENT_HTTP_RATE_LIMIT_MAX_CALLS=240
SENTIMENT_HTTP_RATE_LIMIT_PERIOD_SEC=60
ML_TARGET_HORIZON_BARS=3
ML_TARGET_MIN_EDGE_MULTIPLIER=2.0
ML_WF_VALIDATION_ENABLED=1
ML_WF_SPLITS=5
ML_WF_MIN_ACCURACY=0.52
SWING_WEIGHT_HTF_TREND=2
SWING_WEIGHT_MARKET_REGIME=1
SWING_WEIGHT_EMA_STACK=2
SWING_WEIGHT_PRICE_VS_TREND_EMA=1
SWING_WEIGHT_FRESH_CROSS=1
SWING_WEIGHT_RSI=1
SWING_WEIGHT_MACD=1
SWING_WEIGHT_ADX=1
SWING_WEIGHT_VOLUME_CONFIRM=1
SWING_WEIGHT_TREND_STRENGTH=1
SWING_WEIGHT_HIGH_VOL_PENALTY=1
SCALP_WEIGHT_CROSS=4
SCALP_WEIGHT_RSI=2
SCALP_WEIGHT_HTF=1
SCALP_WEIGHT_REGIME_BONUS=1
SCALP_WEIGHT_VOLUME_CONFIRM=1
DUST_CLEANUP_MIN_REMAINING_PCT=12.0
BACKTEST_SLIPPAGE_BPS=6
BACKTEST_SPREAD_BPS=4
BACKTEST_SPREAD_MAX_PCT=1.0
BACKTEST_WF_OPTIMIZE_PER_WINDOW=1
BACKTEST_WF_OPTIMIZE_TRIALS=6
CRYPTO_API_RATE_LIMIT_MAX_CALLS=1100
CRYPTO_API_RATE_LIMIT_PERIOD_SEC=60
STOCK_YF_LOOKBACK_PERIOD=18mo
OPENBB_ENABLED=1
OPENBB_ONLY=0
OPENBB_STRICT=0
OPENBB_PROVIDER_STOCK=yfinance
OPENBB_PROVIDER_CRYPTO=yfinance
STOCK_DATA_PROVIDER=yfinance
WEB_MARKET_ALLOW_BLOCKING_FALLBACK=1
TRADING_COMMAND_POP_MAX_BATCH=1000
MARKET_ORDER_MAX_SPREAD_PCT=0.5
STOCK_SCREEN_WORKERS=4
STOCK_SCREEN_LIMIT=12
STOCK_MIN_PRICE=50
STOCK_MAX_PRICE=100000
STOCK_ATR_PCT_MIN=1.0
STOCK_MIN_LIQUIDITY_IDR=1000000000
STOCK_VOLUME_LOT_MULT=1.0
STOCK_VOL_SPIKE_MULT=1.5
STOCK_VCP_STD_PCT=0.035
STOCK_RS_DAYS=60
STOCK_MAX_DER=1.0
STOCK_MIN_ROE=10.0
CRYPTO_SCREEN_USE_OPENBB=0
CRYPTO_SCREEN_ALLOW_YFINANCE_FALLBACK=1
CRYPTO_YF_LOOKBACK_PERIOD=60d
CRYPTO_SCREEN_EXCHANGE_REUSE_SEC=180
CRYPTO_SCREEN_YF_INTERVAL_SYNC=1
STOCK_SCREEN_NETWORK_RETRIES=3
STOCK_SCREENER_CHART_WIDTH=1400
STOCK_SCREENER_CHART_HEIGHT=860
STOCK_SCORE_MIN_BASE=45
STOCK_SCORE_MIN_BEAR_EXTRA=3
STOCK_SCORE_BULL_BONUS=2
STOCK_SCORE_BEAR_PENALTY=3
STOCK_SCORE_BENCH_BULL_PCT=2.5
STOCK_SCORE_BENCH_BEAR_PCT=2.5
STOCK_HIT_PROB_WEIGHT=0.35
STOCK_HIT_PROB_LOOKBACK=60
STOCK_KELLY_MAX=0.25
STOCK_HURST_WINDOW=90
STOCK_TREND_SLOPE_FAST=20
STOCK_TREND_SLOPE_SLOW=60
STOCK_CVAR_ALPHA=0.05
CRYPTO_HIT_PROB_WEIGHT=0.35
CRYPTO_HIT_PROB_LOOKBACK=120
CRYPTO_KELLY_MAX=0.30
CRYPTO_HURST_WINDOW=140
CRYPTO_TREND_SLOPE_FAST=24
CRYPTO_TREND_SLOPE_SLOW=72
CRYPTO_CVAR_ALPHA=0.05
HEALTH_HTTP_TIMEOUT_SEC=5.0
AUTOMATION_AC_TURBO_COOLDOWN_SEC=300
SELF_DEPLOY_VALIDATE_COMPILE=1
SELF_DEPLOY_COMPILE_TARGETS=apps,core,skills,database,runtime,utils,main.py
SELF_DEPLOY_VALIDATE_TESTS=0
SELF_DEPLOY_VALIDATE_TEST_CMD=
SELF_DEPLOY_VALIDATE_EXTRA_CMD=

# Integrations (fill if used)
OPENAI_API_KEY=
NVIDIA_API_KEY=
GROQ_API_KEY=
GEMINI_API_KEY=
LLM_PRIMARY_PROVIDER=groq
LLM_NLU_PROVIDERS=groq,gemini,openai
LLM_ALLOW_NO_API_KEY_IN_DEV=1
LLM_REQUIRE_PRIMARY_API_KEY=0
LLM_BASE_URL=
LLM_LOCAL_API_KEY=
OPENAI_MODEL=gpt-4o-mini
OPENAI_MAX_TOKENS=0
OPENAI_TOP_P=1.0
OPENAI_PRESENCE_PENALTY=0.0
OPENAI_TOP_K=0
OPENAI_REPETITION_PENALTY=0.0
OPENAI_ENABLE_THINKING=0
TELEGRAM_BOT_TOKEN=
TG_ALLOWED_USER=0
TELEGRAM_LOG_THROTTLE_WINDOW_SEC=60
TELEGRAM_LOG_THROTTLE_BURST=5
TELEGRAM_LOG_CIRCUIT_BREAKER_THRESHOLD=5
TELEGRAM_LOG_CIRCUIT_BREAKER_COOLDOWN_SEC=120

# NVIDIA OpenAI-compatible quick setup:
# LLM_PRIMARY_PROVIDER=openai
# LLM_BASE_URL=https://integrate.api.nvidia.com/v1
# OPENAI_MODEL=qwen/qwen3.5-397b-a17b
# OPENAI_TOP_K=20
# OPENAI_REPETITION_PENALTY=1.0
# OPENAI_ENABLE_THINKING=1

# Web hardening
WEB_SECURITY_HEADERS=1
WEB_CSRF_PROTECT=1
WEB_FORCE_HTTPS=0
WEB_HTML_AUTH_GUARD_ENABLED=1
WEB_REQUIRE_EMAIL_VERIFICATION=0
WEB_EXPOSE_DEV_AUTH_TOKENS=0
WEB_PASSWORD_RESET_TTL_MINUTES=30
WEB_EMAIL_VERIFY_TTL_MINUTES=1440
WEB_MFA_TOTP_PERIOD_SEC=30
WEB_MFA_TOTP_WINDOW_STEPS=1
WEB_ADMIN_MFA_REQUIRED=1
WEB_REVERSE_PROXY_ENABLED=0
WEB_REVERSE_PROXY_HOPS=1
WEB_REVERSE_PROXY_REQUIRED_IN_PROD=1
WEBHOOK_CLIENT_IP_MAX_LEN=128
WEB_ALLOWED_HOSTS=localhost,127.0.0.1
WEB_MAX_COMMAND_CHARS=1000
WEB_MAX_REASON_CHARS=500
WEB_MAX_EMAIL_CHARS=254
WEB_MAX_PASSWORD_CHARS=256
WEB_ENABLE_API_V1_ALIASES=1
CORS_ORIGINS=http://localhost:5000
WEB_RATE_LIMIT_DEFAULT=120 per minute
WEB_RATE_LIMIT_STATUS=60 per minute
WEB_RATE_LIMIT_TRADING_COMMAND=20 per minute
WEB_RATE_LIMIT_PANIC=5 per minute
WEB_COMMAND_SYNC_TIMEOUT_SEC=2.5
WEB_COMMAND_BACKGROUND_DEFAULT=0
WEB_LOG_REDACT_COMMANDS=1
WEB_DEVICE_SET_MIN=-50
WEB_DEVICE_SET_MAX=100
WEB_HEALTH_PROBE_TIMEOUT_SEC=0.8
WEB_HEALTH_CACHE_TTL_SEC=2.0
WEB_ACTIVITY_ALERT_TG_ENABLED=1
WEB_ACTIVITY_ALERT_COOLDOWN_SEC=90
WEB_ACTIVITY_ALERT_MAX_MEMORY=500
WEB_ACTIVITY_ALERT_DIGEST_SEC=900
WEB_ACTIVITY_ALERT_DIGEST_MIN_EVENTS=3
WEB_ACTIVITY_ALERT_USER_ACTION_COOLDOWN_SEC=300
WEB_SESSION_TOUCH_MIN_INTERVAL_SEC=30
WEB_ACTIVITY_ANOMALY_WINDOW_MIN=5
WEB_ACTIVITY_ANOMALY_FAIL_THRESHOLD=8
WEB_ACTIVITY_ANOMALY_SENSITIVE_SPAM_THRESHOLD=6
WEB_ACTIVITY_ANOMALY_5XX_SPIKE_THRESHOLD=10
WEB_MARKET_CHART_FETCH_INTERVAL_SEC=8
WEB_MARKET_CHART_POINTS_MAX=120
WEB_MARKET_SYMBOLS_MAX=64
WEB_MARKET_DEFAULT_TIMEFRAME=1h
WEB_MARKET_ALERT_EVAL_INTERVAL_SEC=8
WEB_MARKET_ALERT_DEFAULT_COOLDOWN_SEC=300
WEB_MARKET_ALERT_MAX_RULES_PER_USER=200
JWT_REVOKE_ENABLED=1
JWT_BLOCKLIST_FALLBACK_TTL_SEC=86400
JWT_TOKEN_LOCATION=headers,cookies
JWT_ACCESS_COOKIE_NAME=elara_access_token
JWT_COOKIE_SAMESITE=Lax
JWT_COOKIE_CSRF_PROTECT=0
API_REPLAY_PROTECT_ENABLED=0
API_REPLAY_SECRET=
API_REPLAY_MAX_SKEW_SEC=10
API_REPLAY_NONCE_TTL_SEC=30
WEBHOOK_SHARED_SECRET=
WEBHOOK_ALLOW_UNSIGNED=0
WEBHOOK_IDEMPOTENCY_TTL_SEC=120
ML_ARTIFACT_SIGNING_KEY=
ML_REQUIRE_SIGNED_ARTIFACTS=0
ML_ARTIFACT_MAX_BYTES=104857600
ML_ARTIFACT_ALLOWED_CLASS_PREFIXES=sklearn.
ML_ARTIFACT_ALLOW_LEGACY_PICKLE=0
ML_ARTIFACT_ALLOW_EXTERNAL_PATHS=1
# Runtime hardening additions
WEATHER_CACHE_TTL_SEC=90
WEATHER_API_INVALID_BLOCK_SEC=3600

KNOWLEDGE_CHUNK_SIZE=700
KNOWLEDGE_CHUNK_OVERLAP=120
KNOWLEDGE_EMBED_MODEL=all-MiniLM-L6-v2
KNOWLEDGE_EMBED_MODEL_CACHE_DIR=runtime/embedding_models

INTERNET_ROTATING_USER_AGENTS=
SENTIMENT_API_URL=
SENTIMENT_API_TOKEN=

API_REPLAY_SECRET_ROTATION=
WEBHOOK_IDEMPOTENCY_KEY_MAX_LEN=128
WEBHOOK_IDEMPOTENCY_MEMORY_MAX=5000
WEBHOOK_RATE_LIMIT_PER_MIN=120
WEBHOOK_RATE_LIMIT_WINDOW_SEC=60

TG_HANDLER_MAX_CONCURRENCY=4
TG_SKIP_PENDING=1
TG_REJECT_LOG_COOLDOWN_SEC=30
TG_POLLING_CONFLICT_COOLDOWN_SEC=300
TG_POLLING_RESTART_BACKOFF_SEC=3
TG_SKILL_SHORTCUTS_ENABLED=1
TG_SKILL_SHORTCUT_ALIASES=ops=elara-trading-ops,debug=systematic-debugging,verify=verification-before-completion,test=webapp-testing,api=api-testing-observability-api-mock,sec=api-security-best-practices
TELEGRAM_LOG_SUPERVISOR_INTERVAL_SEC=5

VOICE_WATCHDOG_INTERVAL_SEC=5
STREAM_JPEG_QUALITY=55
STREAM_JPEG_QUALITY_MIN=20
STREAM_JPEG_QUALITY_MAX=70
STREAM_JPEG_QUALITY_STEP=5
STREAM_WATCHDOG_INTERVAL_SEC=6

PRESENCE_PING_CONCURRENCY=8
PRESENCE_PING_TIMEOUT_SEC=2.5

BACKUP_RETRY_MAX_ATTEMPTS=3
BACKUP_RETRY_BACKOFF_SEC=3
BACKUP_PG_DUMP_TIMEOUT_SEC=120

SCHEDULER_JOBSTORE_REDIS_URL=
SCHEDULER_REDIS_JOBS_KEY=elara:scheduler:jobs
SCHEDULER_REDIS_RUN_TIMES_KEY=elara:scheduler:run_times
SCHEDULER_LATE_JOB_WARN_SEC=15
SCHEDULER_MISFIRE_GRACE_SEC=90

SELF_HEAL_NOW_MIN_INTERVAL_SEC=20
SELF_HEAL_NOW_ASYNC_DEFAULT=1
SELF_HEAL_CONFIG_BACKUP_ENABLED=1
SELF_HEAL_CONFIG_BACKUP_MAX_FILES=30
SELF_HEAL_CONFIG_BACKUP_RETENTION_DAYS=14
SELF_DEPLOY_AUTO_ROLLBACK=1
SELF_DEPLOY_REQUIRE_SIGNED_COMMITS=0
SELF_DEPLOY_ALLOWED_SIGNATURE_STATES=G,U

TSDB_ASYNC_WRITER_ENABLED=1
TSDB_QUEUE_MAXSIZE=2000
AUTOMATION_THREAD_POOL_WORKERS=1

ML_ARTIFACT_IO_CHUNK_SIZE=1048576

LIFE_REDIS_LOCK_TTL_SEC=10
LIFE_REDIS_LOCK_WAIT_SEC=1.5

NLP_CACHE_CLEANUP_INTERVAL_SEC=120
NLP_INJECTION_MAX_REPEAT_CHARS=48

DB_TX_RETRY_MAX_ATTEMPTS=3
DB_TX_RETRY_BACKOFF_SEC=0.05
DB_TX_FOR_UPDATE_NOWAIT=1
TRADING_METRICS_LOOKBACK_DAYS=0

# Runtime Health & Gateway Fallback
TRADER_GATEWAY_DB_FALLBACK_KEY=trader_gateway:pending_command
HEALTH_POLL_PATH=
HEALTH_POLL_INTERVAL=300

DISABLED_SKILLS=
DISABLED_INTENTS=
```

## Local Development

### Prerequisites

- Python 3.12 for local dev
- PostgreSQL 16 or Docker for DB
- Redis 7 or Docker for cache and event bus
- Git and a Unix like shell or PowerShell

### Install Dependencies

This repo uses `requirements.in` and `requirements-dev.txt`. For local dev, install from `requirements.in`:

```bash
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.in
pip install -r requirements-dev.txt
```

If you want a pinned `requirements.txt` for reproducible builds, use pip-tools or uv to compile `requirements.in` into `requirements.txt`.

### Environment Setup

PowerShell:

```powershell
.\scripts\create_dev_env.ps1
```

Or copy the full template manually:

```powershell
Copy-Item .env.example .env
```

For Docker Compose, keep `.env.docker` present as the container-network
override file. Start from `.env.docker.example` if you need to recreate it:

```powershell
Copy-Item .env.docker.example .env.docker
```

For PostgreSQL/Redis-backed local runs, edit `.env` and set at minimum:

- `SECRET_KEY`
- `JWT_SECRET_KEY`
- `POSTGRES_PASSWORD`
- `REDIS_PASSWORD`

### Start Services Manually

Core only:

```bash
python apps\\elara_core\\main.py
```

Trader only:

```bash
python apps\\elara_trader\\main.py
```

Unified entrypoint (core role by default):

```bash
python main.py
```

Default web UI URL:

- [http://localhost:5000/portal](http://localhost:5000/portal)

Health endpoint:

- [http://localhost:5000/api/health](http://localhost:5000/api/health)

Trader status:

- [http://localhost:8768/status](http://localhost:8768/status)

### One Command Dev Up (Windows)

This repo ships with dev up scripts that start Redis, core, and trader.
The Python entrypoint is thin and delegates to focused modules under `dev_bootstrap/`:

- `service_manager.py` for process and port orchestration
- `health_client.py` for HTTP/contract checks
- `bootstrap_runner.py` for the top-level startup flow

Run it with:

```powershell
.\dev_up.ps1
```

or

```powershell
.\dev_up.cmd
```

## Docker Compose

Docker compose runs Redis, Postgres, Mosquitto, core, and trader:

```bash
docker compose up -d --build
```

Stop:

```bash
docker compose down
```

Important note:

The `Dockerfile` now installs directly from `requirements.in`, so the previous missing `requirements.txt` blocker is removed.

## Scripts and Operational Tools

Common scripts under `scripts/`:

- `quality_gate.ps1` runs lint and type checks
- `deep_python_check.py` compiles and runs tests file by file
- `dev_up.ps1` brings up local core and trader
- `run_shadow_pipeline.py` runs a shadow trading pipeline
- `run_simulation_pipeline.py` runs a simulation pipeline
- `run_strategy_discovery.py` runs strategy discovery
- `screen_bbca_crypto.py` demo screener
- `firewall_daemon.py` runs firewall daemon
- `backup_cron.sh` and `run_backup.py` manage backups

## Testing and Quality Gates

## MVP Scope

Elara's MVP is a governed, replayable paper-trading decision system—not a
general assistant or live trader. The authoritative definition, scope freeze,
current gap register, and release gates are in
[`docs/mvp_definition.md`](docs/mvp_definition.md).

Run the deterministic MVP paper path against a CSV containing `timestamp`,
`open`, `high`, `low`, `close`, and `volume`:

```powershell
python scripts/run_paper_mvp.py .\market.csv --symbol BTC/USDT
```

Replay an audited decision:

```powershell
python scripts/replay_paper_decision.py <trace-id>
```

Recommended test commands:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

Quality gate:

```powershell
.\scripts\quality_gate.ps1
```

Deep scan:

```powershell
python .\scripts\deep_python_check.py --root .
```

## Deployment Notes

This repo is Docker first. For production:

- Run core and trader as separate services
- Require Redis and PostgreSQL
- Disable file fallbacks for IPC and heartbeat
- Enable reverse proxy guard

## Troubleshooting

Common issues:

- Redis not reachable: check `REDIS_URL` and `REDIS_PASSWORD`
- DB errors: ensure PostgreSQL is running and `DATABASE_URL` is correct
- LLM providers missing: set `OPENAI_API_KEY`, `GROQ_API_KEY`, or `GEMINI_API_KEY`
- OpenBB unavailable: disable OpenBB or install it
- Web not accessible: ensure `PORT` and `WEB_ALLOWED_HOSTS` are correct

## File Inventory

This workspace was reconstructed from `ELARA_FULL_CODE.txt` and then made self-contained with local development support files.

Current source snapshot:

- Original bundled files restored: 274
- Missing original bundled files: 0
- Added support files include `.env.example`, `.env.docker.example`, `Dockerfile`, dependency files, and local scripts
- Runtime/cache artifacts such as `__pycache__/`, logs, and local state are intentionally ignored

Useful inventory commands:

```powershell
Get-ChildItem -Recurse -File | Measure-Object
Get-ChildItem -Recurse -File | Select-Object -ExpandProperty FullName
```

## License

License is not specified in this repository. If you plan to distribute or deploy this project publicly, add a license file and update this section.

