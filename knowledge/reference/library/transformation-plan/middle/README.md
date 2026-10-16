# Transformation map reference — middle level (ERP open-item clearing, read-only agent in the loop)

**Stage:** `transformation-plan` (Agree the agent's role and authority). **Level:** middle. **Primary artifact:**
[`transformation-map.md`](transformation-map.md).

## What it shows

The present-to-future disposition of every current-state step for this agent, with the two placement columns
(Shape Classification, Sub-Agent Group) and the Sub-Agent Groups section, bounded by the ratified remit. It is the
bridge the future state is drawn from.

## Why this level

A middle-complexity agent: a scheduled read-only agent groups open items and briefs the analyst, and a second, analyst-started exception seam triages the clearing log. Two judgment units, no agent writes, several decision points and loops in the current state.

## Final shape a follower reproduces

`templates/transformation-map.template.md`, section by section:
1. **Per-node disposition table**: one row per current-state step, first column the step's node id; the run's own base
   disposition column; `Shape Classification` (`script-eligible` / `judgment-required`, agent-taken rows only);
   `Sub-Agent Group` (judgment-required rows only); detail with the evidence state in prose and the future elements
   each step becomes. Then a tally that reconciles to the current-state step count, counted by script.
2. **Sub-Agent Groups**: one row per group, with handoff in, handoff out (target and gating decision) and basis.
3. New future-state additions. 4. Judgment-model statement. 5. Authority boundary. 6. Retained controls for people.
7. What We Still Need to Know, each item with its gap-closure route.

Check: `checks/check-transformation-map-shape.sh` passes; derived placement: **multi-subagent-with-handoffs**.

## What is anonymized and how

The client organisation is Acme; the ERP is ERP (clearing), its programs and apps are named by role (ERP clearing program, ERP line-item app); company codes are AC codes; clearing and posting-period variant names, places and the downstream client entity are neutral labels; amounts, G/L accounts, assignment and document numbers are synthetic in the same format; the process id `ac4ea12e7a66` (and its dependency `c0a6b3a6d555`) is kept, every other Company Brain key is synthetic. Commodity tools (Excel, Outlook, Teams) keep their names.

## Related references at this level

- Company Brain Snapshot: [`../../snapshot/middle/snapshot-intake.json`](../../snapshot/middle/snapshot-intake.json)
- Current State Workflow: [`../../current-state/middle/current-state.json`](../../current-state/middle/current-state.json)
- Future State Workflow: [`../../future-state/middle/future-state.json`](../../future-state/middle/future-state.json)
