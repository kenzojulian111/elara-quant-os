# Cognitive Boundaries

Elara's cognition layers are allowed to collaborate, but they are not allowed to blur responsibilities. This file defines the local constitution for subsystem boundaries.

## Portfolio Brain

Must:

- select or suppress strategy allocation
- expose allocation reasons
- keep concentration risk visible

Must never:

- submit execution
- bypass risk or governance
- mutate runtime health

## Attention Engine

Must:

- decide where compute and strategy focus should go
- degrade optional work under pressure
- expose skipped and focused strategy ids

Must never:

- mutate portfolio state
- trigger execution
- override governance decisions

## Objective Engine

Must:

- translate operator intent into decision bias
- shift toward capital preservation under unsafe runtime modes
- expose confidence floor and objective reasons

Must never:

- execute trades
- hide objective changes from the pipeline trace
- silently override strategy action

## Temporal Reasoner

Must:

- track short-horizon regime memory
- surface loss streaks and persistence
- block or degrade entries when temporal context is unsafe

Must never:

- persist execution state
- mutate positions directly
- erase prior decision reasons

## Decision Core

Must:

- collect the raw strategy signal
- build candidates and execution plans
- call risk before an entry becomes executable

Must never:

- skip trace id creation
- approve an entry without risk evaluation
- treat governance as optional once runtime context exists

## Risk Engine

Must:

- validate position sizing and drawdown constraints
- cap or block unsafe entries
- explain every rejection

Must never:

- change the strategy signal
- submit orders
- ignore current drawdown

## Runtime Assessment

Must:

- classify dependency and system health
- expose safe, degraded, or full runtime mode
- feed governance before final approval

Must never:

- approve execution directly
- hide stale dependency state
- downgrade critical dependency failures into cosmetic warnings

## Decision Governor

Must:

- enforce operator, dependency, market, and runtime gates
- block new entries when runtime mode requires it
- preserve exit permission when safety allows

Must never:

- mutate the raw signal
- create an execution plan
- run before runtime mode is known

## Decision Reasoner

Must:

- synthesize final explainable action
- attach probability, uncertainty, and expected utility
- close the pipeline trace with `reasoning`

Must never:

- erase earlier trace steps
- approve an entry without an execution plan
- hide veto constraints

## Rule

When a layer needs behavior outside its boundary, add an explicit contract between layers instead of smuggling state through shared mutable objects.
