# Elara Dashboard Reboot Plan

## Problem Statement

Elara currently has three overlapping UI surfaces:

1. `index.html` as a legacy dashboard
2. `portal.html` as a newer portal
3. `terminal.html` as a separate terminal view

This creates structural duplication instead of a coherent product.

## Evidence

### Route duplication

In [web_factory.py](D:/codingan/elara2/runtime/interfaces/web_factory.py):

- `/` renders `index.html`
- `/dashboard` renders `index.html`
- `/portal` renders `portal.html`
- `/terminal` renders `terminal.html`
- `/dashboard/user` and `/dashboard/admin` render `portal.html`

This means one product is being represented by three different UI concepts.

### Template sprawl

Current template size:

- `templates/index.html`: 1461 lines
- `templates/portal.html`: 2872 lines
- `templates/terminal.html`: 854 lines

Current CSS size:

- `static/css/elara-dashboard-base.css`: 571 lines
- `static/css/elara-dashboard-ux.css`: 1006 lines
- `static/css/elara-portal.css`: 1537 lines
- `static/css/elara-terminal.css`: 496 lines

The current UI system is too fragmented to evolve cleanly.

### Runtime blocker

The app still imports `config.settings` from multiple modules, but `config/settings.py` does not exist.

This is a hard structural issue and must be fixed before any UI reboot can be considered healthy.

## Decision

Elara should move to a single dashboard architecture.

### Keep

- `login.html`
- `account.html`
- one unified dashboard shell

### Remove or deprecate

- `index.html` as a separate legacy dashboard
- `terminal.html` as a separate route concept
- "Open Legacy Dashboard" navigation path

### New rule

There must be exactly one main authenticated workspace for operators.

## Target Product Model

### Surface model

Use one authenticated workspace with mode switching inside the same shell:

1. `Overview`
2. `Trading`
3. `Market`
4. `Operations`

Not three different pages pretending to be different products.

### Route model

Recommended target:

- `/` -> redirect to `/app`
- `/dashboard` -> redirect to `/app`
- `/portal` -> redirect to `/app`
- `/terminal` -> redirect to `/app?mode=trading`
- `/dashboard/user` -> redirect to `/app?role=user`
- `/dashboard/admin` -> redirect to `/app?role=admin`
- `/app` -> unified dashboard shell

The old routes can stay temporarily for backward compatibility, but they should all resolve to one UI.

## Visual Direction

### Design stance

Name: `Calm Bento Terminal`

Tone:

- minimalist
- editorial-tech

Not:

- flashy gamer dashboard
- fake Bloomberg clone
- heavy neon trading UI

### Design goal

The UI should feel:

- calm
- sharp
- dense but breathable
- premium without visual noise

### Differentiation anchor

Elara should be recognizable by:

- a disciplined bento grid
- restrained typography
- soft dark surfaces with warm neutral highlights
- one strong command rail
- one market pulse panel that feels alive without being chaotic

## Layout Direction

### Core dashboard sections

Use one bento grid with fixed semantic areas:

1. Hero strip
- session identity
- system mode
- market state
- kill switch status

2. Trading control card
- allowed commands
- risk state
- broker/execution state

3. Market pulse
- top movers
- spread/liquidity signal
- watchlist

4. Decision log
- latest decisions
- why a trade is blocked or allowed

5. Exposure and health
- equity
- drawdown
- open risk
- CPU, memory, queue lag

6. Alerts and incidents
- live alerts
- watchdog events
- degraded mode banner

7. Operator notes or activity feed
- only if it stays high signal

### Remove from the main view

- duplicated navigation paths
- oversized hero animation
- decorative complexity that does not improve decisions
- split identity between "portal", "legacy", and "terminal"

## Technical Direction

### UI architecture

Move to:

- one main template for the authenticated app
- shared design tokens
- smaller JS modules by concern

Recommended split:

1. `templates/app.html`
2. `static/css/elara-app.css`
3. `static/js/app-shell.js`
4. `static/js/app-market.js`
5. `static/js/app-trading.js`
6. `static/js/app-ops.js`

### Why

The current monolithic template approach makes every change risky.

## Functional Direction

### Non-negotiable product behavior

1. If Elara cannot chat, it must visibly fall back.
2. If a command cannot execute, the UI must explain why.
3. If trading is blocked, the reason must be rendered in the dashboard.
4. If market data is stale, the dashboard must enter degraded mode clearly.

The dashboard is not just a skin. It must expose state truthfully.

## Phased Implementation

### Phase 0: Stabilize runtime

1. Restore `Config` import path consistency.
2. Make app bootstrap importable.
3. Get one minimal health/test path green again.

### Phase 1: Route consolidation

1. Introduce `/app`.
2. Redirect legacy routes into `/app`.
3. Remove legacy-first navigation buttons.

### Phase 2: UI shell reboot

1. Build one new bento grid shell.
2. Reuse only essential data panels.
3. Strip non-essential ornamentation.

### Phase 3: Function alignment

1. Make command bus explain success, block, or fallback.
2. Surface trading state, risk state, and stale feed state clearly.
3. Fold terminal-only widgets into the same app shell.

### Phase 4: Cleanup

1. Delete old dashboard-specific CSS once migrated.
2. Delete dead legacy markup.
3. Keep compatibility redirects only if still needed.

## Recommended Priority

If only one thing is done first, do this:

1. Fix `config.settings` breakage
2. Consolidate routes to one dashboard
3. Rebuild UI shell on top of that

That order is mandatory. A visual redesign on top of a broken runtime will just hide problems.

