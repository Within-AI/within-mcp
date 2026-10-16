# Future State Workflow reference — complex level (Bank remittance enrichment and posting)

**Stage:** `future-state`. **Level:** complex. **Primary artifact:** [`future-state.json`](future-state.json), the rendered v2
workflow bundle, alongside [`future-state.svg`](future-state.svg) and the standalone page [`future-state.html`](future-state.html).

## What it shows

The Future State Workflow of this agent in the plugin's v2 workflow format, drawn by `renderer/render_workflow.py`: people's steps in the Analyst band, the agent's in the Agent band;
every system is a rail and every read or write is a drop onto it. Files: `future-state.html`, `future-state.input.json`, `future-state.json`, `future-state.svg`. `future-state.input.json` is the
renderer's input, which the output bundle pins as `prior_workflow`.

## Why this level

The most complex shape: several agent units with handoffs to the analyst, four decision tables, split steps, a re-proposal loop, contradictory evidence and a long list of open questions. It shows how far the format stretches without special cases.

## Final shape a follower reproduces

- **Envelope**: `schema_version: 2`, `kind: workflow`, `variant: future-state`, `process` (`process_ids` equal to the
  snapshot scope), `snapshot` pin (path + SHA-256 of `../../snapshot/complex/snapshot.json`), `presentation: diagram`,
  `diagram` (title, notes), `graph`, `coverage`, and the renderer-written `render`, `render_html`,
  `render_coverage`, `prior_workflow`.
- **Graph**: 22 steps in bands Agent, Analyst, System; 9 system rails; 53 edges (else 2, exception 2, handoff 3, loop 2, read_access 19, sequence 13, then 4, write 8); decision tables
  `dm-source-state-invoice-identity`, `dm-source-state-supporting-evidence`, `dm-allocation-method`, `dm-correction-unresolved`, `dt-n-010`; `starts`, `outcomes`, `open_ends`; 15 open issues.
- **Evidence**: every node, edge, table, row and rule carries `evidence_state` (Observed, Assumed, Contradictory,
  `needs_more_information`) and `source_refs` pointing at an exact location in the frozen snapshot, or an
  `issue_id` / `issue_ids` naming an open issue.
- **Decision tables**: every decision step names its table; a selection table lists each exit in exactly one row.
- **Coverage**: one row per captured step (16, 16 mapped to graph elements).
- **Systems coverage**: `systems_coverage` has one row per system in the snapshot (13, all mapped onto a rail or workspace of the graph); the `snapshot` pin is the new `snapshot.json`.
- **Current-state link**: `current_state` pins `../../current-state/complex/current-state.json` by path and SHA-256
  (`kind: workflow-bundle`); every current-state issue is carried unchanged (carry check), and a dropped or changed
  decision table is only a page note.
- **Ownership**: every person or agent step states a `treatment` (`person-kept`, `agent-taken`, `split`) and every rail an
  `access_state`.
- **Design elements**: an element with no current-state antecedent is bound to the open issue
  `introduced-by-design`; it stays open until the design is ratified against evidence.

Checks: `workflow-graph-structure`, `workflow-coverage-manifest`, `workflow-open-and-contradiction`,
`workflow-render-binding` and `workflow-render-geometry` pass; the SVG and HTML redraw byte-identically from the
bundle.

## What is anonymized and how

The client organisation is Acme; the bank the client deals with is Bank and its systems are named by role (Bank portal, Bank statement report); the ERP is ERP (receipts); client-built apps are named by role (Invoicing app, Company records app, Collections workbench); payers are synthetic SYN names; amounts and document numbers are synthetic in the same format; the process id `5bbb985d60a7` (and its dependency `a88919d7bd0c`) is kept, every other Company Brain key is synthetic. A path to another client's material was removed from the legacy future state. The graph was converted from the agent's own earlier graph, after anonymizing that graph with the same
substitution ledger as the snapshot, so names agree across the chain.

## Related references at this level

- Company Brain Snapshot: [`../../snapshot/complex/snapshot-intake.json`](../../snapshot/complex/snapshot-intake.json)
- Current State Workflow: [`../../current-state/complex/current-state.json`](../../current-state/complex/current-state.json)
- transformation map: [`../../transformation-plan/complex/transformation-map.md`](../../transformation-plan/complex/transformation-map.md)

## Internal-handoff record

The reviewed future state passes to the design half as one `handoff.internal` event on the run log
(`reference/event-log-schema.md`: required payload `to_stage` and `provenance`; the record also names the stage it
comes from and pins the exact bundle by path and SHA-256). This level's record:

```json
{
  "event_id": "evt-0001",
  "ts": "2026-09-24T00:00:00Z",
  "run_id": "reference-library-complex",
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
    "artifact_sha256": "11a30552ef8d373fa14f65de73b07272ba78a08c7a1aa0207714757e9ff2efb3"
  }
}
```
