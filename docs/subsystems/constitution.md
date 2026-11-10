# Subsystem Constitution

Each subsystem must have:

- purpose
- authority
- forbidden actions
- owned state
- produced events
- consumed events
- failure semantics

Run:

```powershell
python scripts/check_subsystem_constitution.py
python scripts/check_subsystem_constitution.py --json
python scripts/check_subsystem_constitution.py --fail-on-critical
```

## Why

Elara's biggest long-term risk is not lack of intelligence. It is subsystem overlap that creates hidden authority.

The constitution makes authority explicit:

- Portfolio may allocate, but must not execute.
- Objective may bias, but must not hide objective shifts.
- Temporal may block unsafe context, but must not mutate positions.
- Decision Core may build plans, but must not bypass governance.
- Governance may block or approve, but must not create execution plans.
- Reasoning may synthesize, but must not erase trace history.

## Rule

When a subsystem needs authority outside its constitution, add a contract or move the behavior to the owning layer.
