# Return: <agent name>

Written by `within:agent-build` at the end of the build, from the exit gate's records.

## Status
Status: `done` or `not done`
Reasons: <one line per reason; for `not done`, each exit check or residual that blocks>

## Per-process coverage
One row per process: each exit check's result (`pass`, `fail` or `unknown`).

| Process | Frozen expected | Early end-to-end | Fire and no-fire | Numbers by code | Live run | Light cold run | Client clearance |
|---|---|---|---|---|---|---|---|
| <process id> | <result> | <result> | <result> | <result> | <result> | <result> | <result> |

## Residuals

### Blocks landing
- <residual id> — <kind> — <summary>

### Lands with a note
- <residual id> — <kind> — <summary>

## What we still need to know
- <open item id> — <question> — <safe path meanwhile: stop or escalate>

## Hand-over list

### What was stubbed
- <system> — <why it was stubbed>

### What to run when access arrives
- <command or step> — <which system it unlocks>

### What the customer integrates
- <integration point> — <owner on the customer side>

## Next: harden the evals
See `references/eval-hardening.md`. After the agent is built, grow a capability suite and a regression suite, and calibrate the judges where judged checks matter.

## Scope
The skill stops before production launch. Launching the agent is the customer's step.
