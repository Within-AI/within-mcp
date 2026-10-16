# Transformation map reference — complex level (Bank remittance enrichment and posting)

**Stage:** `transformation-plan` (Agree the agent's role and authority). **Level:** complex. **Primary artifact:**
[`transformation-map.md`](transformation-map.md).

## What it shows

The present-to-future disposition of every current-state step for this agent, with the two placement columns
(Shape Classification, Sub-Agent Group) and the Sub-Agent Groups section, bounded by the ratified remit. It is the
bridge the future state is drawn from.

## Why this level

The most complex shape: several agent units with handoffs to the analyst, four decision tables, split steps, a re-proposal loop, contradictory evidence and a long list of open questions. It shows how far the format stretches without special cases.

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

The client organisation is Acme; the bank the client deals with is Bank and its systems are named by role (Bank portal, Bank statement report); the ERP is ERP (receipts); client-built apps are named by role (Invoicing app, Company records app, Collections workbench); payers are synthetic SYN names; amounts and document numbers are synthetic in the same format; the process id `5bbb985d60a7` (and its dependency `a88919d7bd0c`) is kept, every other Company Brain key is synthetic. A path to another client's material was removed from the legacy future state.

## Related references at this level

- Company Brain Snapshot: [`../../snapshot/complex/snapshot-intake.json`](../../snapshot/complex/snapshot-intake.json)
- Current State Workflow: [`../../current-state/complex/current-state.json`](../../current-state/complex/current-state.json)
- Future State Workflow: [`../../future-state/complex/future-state.json`](../../future-state/complex/future-state.json)
