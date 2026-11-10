# Architecture Heatmap

The architecture heatmap is a lightweight observability layer for the codebase itself.

It ranks modules using simple pressure signals:

- non-empty line count
- import density
- config and settings mentions
- decision trace mentions
- event ledger and stream mentions

Run:

```powershell
python scripts/check_architecture_heatmap.py
python scripts/check_architecture_heatmap.py --json
```

## Current Hotspots

These are not automatically bad modules. They are modules that deserve extra caution during edits because they concentrate operational meaning.

```text
config/settings.py
runtime/interfaces/web_factory.py
trading_core/orchestration/decision_core.py
trading_core/orchestration/shadow_pipeline.py
utils/self_ops.py
trading_core/orchestration/decision_reasoner.py
runtime/interfaces/telegram_service.py
runtime/interfaces/orchestrator_service.py
```

## Rule

If a hotspot keeps growing, prefer one of:

- remove obsolete behavior
- extract a typed contract
- add replay coverage
- add a topology or boundary note
- split only when the split reduces operator confusion
