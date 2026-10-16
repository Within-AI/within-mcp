# Transformation map reference — simple level (Weekly metrics pack refresh)

**Stage:** `transformation-plan` (Agree the agent's role and authority). **Level:** simple. **Primary artifact:**
[`transformation-map.md`](transformation-map.md).

## What it shows

The present-to-future disposition of every current-state step for this agent, with the two placement columns
(Shape Classification, Sub-Agent Group) and the Sub-Agent Groups section, bounded by the ratified remit. It is the
bridge the future state is drawn from.

## Why this level

A pure deterministic script with no model step: it reads one report, writes a Sheet and a Slides review copy and records a run log, while people keep the screenshot and distribution steps. It is the smallest shape an agent can take, so it shows what the stage looks like with nothing extra.

## Final shape a follower reproduces

`templates/transformation-map.template.md`, section by section:
1. **Per-node disposition table**: one row per current-state step, first column the step's node id; the run's own base
   disposition column; `Shape Classification` (`script-eligible` / `judgment-required`, agent-taken rows only);
   `Sub-Agent Group` (judgment-required rows only); detail with the evidence state in prose and the future elements
   each step becomes. Then a tally that reconciles to the current-state step count, counted by script.
2. **Sub-Agent Groups**: one row per group, with handoff in, handoff out (target and gating decision) and basis.
3. New future-state additions. 4. Judgment-model statement. 5. Authority boundary. 6. Retained controls for people.
7. What We Still Need to Know, each item with its gap-closure route.

Check: `checks/check-transformation-map-shape.sh` passes; derived placement: **script**.

## What is anonymized and how

The client organisation is Acme (the workspace name says so); the Company Brain process id `d42bcd549db7` is kept; every other Company Brain key (workspace, version, hierarchy, task, observation, Companion session) is synthetic in the same shape. People appear only as roles. Commodity tools (Salesforce, Google Slides and Sheets, Slack) keep their names.

## Related references at this level

- Company Brain Snapshot: [`../../snapshot/simple/snapshot-intake.json`](../../snapshot/simple/snapshot-intake.json)
- Current State Workflow: [`../../current-state/simple/current-state.json`](../../current-state/simple/current-state.json)
- Future State Workflow: [`../../future-state/simple/future-state.json`](../../future-state/simple/future-state.json)
