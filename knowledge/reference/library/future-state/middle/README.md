# Future State Workflow reference — middle level (ERP open-item clearing, read-only agent in the loop)

**Stage:** `future-state`. **Level:** middle. **Primary artifact:** [`future-state.json`](future-state.json), the rendered v2
workflow bundle, alongside [`future-state.svg`](future-state.svg) and the standalone page [`future-state.html`](future-state.html).

## What it shows

The Future State Workflow of this agent in the plugin's v2 workflow format, drawn by `renderer/render_workflow.py`: people's steps in the Analyst band, the agent's in the Agent band;
every system is a rail and every read or write is a drop onto it. Files: `future-state.html`, `future-state.input.json`, `future-state.json`, `future-state.svg`. `future-state.input.json` is the
renderer's input, which the output bundle pins as `prior_workflow`.

## Why this level

A middle-complexity agent: a scheduled read-only agent groups open items and briefs the analyst, and a second, analyst-started exception seam triages the clearing log. Two judgment units, no agent writes, several decision points and loops in the current state.

## Final shape a follower reproduces

- **Envelope**: `schema_version: 2`, `kind: workflow`, `variant: future-state`, `process` (`process_ids` equal to the
  snapshot scope), `snapshot` pin (path + SHA-256 of `../../snapshot/middle/snapshot.json`), `presentation: diagram`,
  `diagram` (title, notes, band_labels), `graph`, `coverage`, and the renderer-written `render`, `render_html`,
  `render_coverage`, `prior_workflow`.
- **Graph**: 17 steps in bands Agent, Analyst, System; 3 system rails; 22 edges (escalation 4, exception 1, handoff 1, inbound 1, read_access 2, reentry 1, sequence 9, then 2, write 1); decision tables
  `dm-signal-label`, `dt-n-07`, `dt-n-14`; `starts`, `outcomes`, `open_ends`; 19 open issues.
- **Systems coverage**: `systems_coverage` has one row per system in the snapshot's inventory (6): ERP (clearing) `mapped` onto its
  rail (the agent's read-only open-item access) and five `cut`, each with the reason it leaves the future workflow (replaced by the agent, or
  retained by the analyst outside the agent's seams and not redrawn). Check: `workflow-systems-coverage`.
- **Evidence**: every node, edge, table, row and rule carries `evidence_state` (Observed, Assumed, Contradictory,
  `needs_more_information`) and `source_refs` pointing at an exact location in the frozen snapshot, or an
  `issue_id` / `issue_ids` naming an open issue.
- **Decision tables**: every decision step names its table; a selection table lists each exit in exactly one row.
- **Coverage**: one row per captured step (15, 14 mapped to graph elements; the rest are summary rows for steps the transformation map retires or folds).
- **Current-state link**: `current_state` pins `../../current-state/middle/current-state.json` by path and SHA-256
  (`kind: workflow-bundle`); every current-state issue is carried unchanged (carry check), and a dropped or changed
  decision table is only a page note.
- **Ownership**: every person or agent step states a `treatment` (`person-kept`, `agent-taken`, `split`) and every rail an
  `access_state`.
- **Design elements**: an element with no current-state antecedent is bound to the open issue
  `introduced-by-design`; it stays open until the design is ratified against evidence.

Checks: `workflow-systems-coverage`, `workflow-graph-structure`, `workflow-coverage-manifest`, `workflow-open-and-contradiction`,
`workflow-render-binding` and `workflow-render-geometry` pass; the SVG and HTML redraw byte-identically from the
bundle.

## What is anonymized and how

The client organisation is Acme; the ERP is ERP (clearing), its programs and apps are named by role (ERP clearing program, ERP line-item app); company codes are AC codes; clearing and posting-period variant names, places and the downstream client entity are neutral labels; amounts, G/L accounts, assignment and document numbers are synthetic in the same format; the process id `ac4ea12e7a66` (and its dependency `c0a6b3a6d555`) is kept, every other Company Brain key is synthetic. Commodity tools (Excel, Outlook, Teams) keep their names. The graph was converted from the agent's own earlier graph, after anonymizing that graph with the same
substitution ledger as the snapshot, so names agree across the chain.

## Related references at this level

- Company Brain Snapshot: [`../../snapshot/middle/snapshot-intake.json`](../../snapshot/middle/snapshot-intake.json)
- Current State Workflow: [`../../current-state/middle/current-state.json`](../../current-state/middle/current-state.json)
- transformation map: [`../../transformation-plan/middle/transformation-map.md`](../../transformation-plan/middle/transformation-map.md)

## Internal-handoff record

The reviewed future state passes to the design half as one `handoff.internal` event on the run log
(`reference/event-log-schema.md`: required payload `to_stage` and `provenance`; the record also names the stage it
comes from and pins the exact bundle by path and SHA-256). This level's record:

```json
{
  "event_id": "evt-0001",
  "ts": "2026-09-24T00:00:00Z",
  "run_id": "reference-library-middle",
  "stage": "internal-handoff",
  "type": "handoff.internal",
  "actor": {
    "role": "orchestrator",
    "engine": "script",
    "model_config": null
  },
  "payload": {
    "to_stage": "agent-backbone",
    "provenance": "same-run",
    "artifact_object_kind": "future-state-workflow",
    "from_stage": "future-state",
    "artifact_path": "future-state.json",
    "artifact_sha256": "d6c9373b256cc15e53e5041aeb38f4433b10a53997161dca4d2609d1267eace2"
  }
}
```
