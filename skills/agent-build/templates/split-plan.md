# Split plan: <agent name>

Split version: <n> (equal to the brief's version)

Split the ratified brief into units. Each unit passes the six unit tests (independent, clear handoff, checkable alone, reviewable, no judgement calls inside, small); a unit that fails one is split again or merged. Fill one row per unit.

The first build round (the smallest `Round`) holds one unit of kind `end-to-end`: the early end-to-end unit on the agreed target, one frozen case through run-this-case.

| # | Unit | Round | Kind | Process | Outcome (true/false) | Owns | Reads / hands off | Independent | Clear handoff | Checkable alone | Reviewable | No judgement calls inside | Small | Blocked by | Estimate (min) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | E2E | 1 | end-to-end | <process id> | One frozen case runs through run-this-case on the agreed target | <paths> | <inputs; hands the thin path to every later unit> | yes | yes | yes | yes | yes | yes | none | <minutes> |

The table's rows match the machine block below; the block is what the exit gate and `e2e.py check-split` read.

```json
{"units": [{"id": "E2E", "round": 1, "kind": "end-to-end"}]}
```

## Set example
For a set of processes: one row per process, plus one shared-agent row naming the processes it serves. Shared blocks are chosen once, in the shared-agent row.

| Unit | Process | Serves | Shared blocks |
|---|---|---|---|
| <unit id> | <process id A> | <process id A> | none |
| <unit id> | <process id B> | <process id B> | none |
| <unit id> | shared agent | <process id A>, <process id B> | <blocks chosen once> |

## Pinned shared surfaces
Each shared surface (a layout, a format, a file several units read) has one named owner. It is pinned before dispatch and must not move for every other unit: they list it under "Must not edit" and read it only.

| Surface | Owner | Pinned at (commit) |
|---|---|---|
| <surface> | <unit id> | <commit> |
