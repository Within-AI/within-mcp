# Future State Workflow reference — simple level (Weekly metrics pack refresh)

**Stage:** `future-state`. **Level:** simple. **Primary artifact:** [`future-state.json`](future-state.json), the rendered v2
workflow bundle, alongside [`future-state.svg`](future-state.svg) and the standalone page [`future-state.html`](future-state.html).

## What it shows

The Future State Workflow of this agent in the plugin's v2 workflow format, drawn by `renderer/render_workflow.py`: people's steps in the Analyst band, the agent's in the Agent band (drawn as Script through `diagram.band_labels`);
every system is a rail and every read or write is a drop onto it. Files: `future-state.html`, `future-state.input.json`, `future-state.json`, `future-state.svg`. `future-state.input.json` is the
renderer's input, which the output bundle pins as `prior_workflow`.

## Why this level

A pure deterministic script with no model step: it reads one report, writes a Sheet and a Slides review copy and records a run log, while people keep the screenshot and distribution steps. It is the smallest shape an agent can take, so it shows what the stage looks like with nothing extra.

## Final shape a follower reproduces

- **Envelope**: `schema_version: 2`, `kind: workflow`, `variant: future-state`, `process` (`process_ids` equal to the
  snapshot scope), `snapshot` pin (path + SHA-256 of `../../snapshot/simple/snapshot.json`), `presentation: diagram`,
  `diagram` (title, notes, band_labels), `graph`, `coverage`, `systems_coverage`, and the renderer-written `render`, `render_html`,
  `render_coverage`, `prior_workflow`.
- **Graph**: 14 steps in bands Agent, Analyst, System; 5 system rails; 27 edges (handoff 1, read_access 6, sequence 12, write 8); decision tables
  (none: no decision step); `starts`, `outcomes`, `open_ends`; 8 open issues.
- **Evidence**: every node, edge, table, row and rule carries `evidence_state` (Observed, Assumed, Contradictory,
  `needs_more_information`) and `source_refs` pointing at an exact location in the frozen snapshot, or an
  `issue_id` / `issue_ids` naming an open issue.
- **Decision tables**: every decision step names its table; a selection table lists each exit in exactly one row.
- **Coverage**: one row per captured step (18, 11 mapped to graph elements; the rest are summary rows for steps the transformation map retires or folds).
- **Systems coverage**: one row per system in the snapshot (3 systems, all mapped: Salesforce, Google Slides and Slack stay in use; none is cut). Rails for a Sheet and a run log are design additions, not snapshot systems, so they carry no row.
- **Current-state link**: `current_state` pins `../../current-state/simple/current-state.json` by path and SHA-256
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

The client organisation is Acme (the workspace name says so); the Company Brain process id `d42bcd549db7` is kept; every other Company Brain key (workspace, version, hierarchy, task, observation, Companion session) is synthetic in the same shape. People appear only as roles. Commodity tools (Salesforce, Google Slides and Sheets, Slack) keep their names. The graph was converted from the agent's own earlier graph, after anonymizing that graph with the same
substitution ledger as the snapshot, so names agree across the chain.

## Related references at this level

- Company Brain Snapshot: [`../../snapshot/simple/snapshot-intake.json`](../../snapshot/simple/snapshot-intake.json)
- Current State Workflow: [`../../current-state/simple/current-state.json`](../../current-state/simple/current-state.json)
- transformation map: [`../../transformation-plan/simple/transformation-map.md`](../../transformation-plan/simple/transformation-map.md)

## Internal-handoff record

The reviewed future state passes to the design half as one `handoff.internal` event on the run log
(`reference/event-log-schema.md`: required payload `to_stage` and `provenance`; the record also names the stage it
comes from and pins the exact bundle by path and SHA-256). This level's record:

```json
{
  "event_id": "evt-0001",
  "ts": "2026-09-24T00:00:00Z",
  "run_id": "reference-library-simple",
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
    "artifact_sha256": "1e1539ec0101284bd86886b3098ac52d8032d32b44e30cc701e0113df459fb29"
  }
}
```
