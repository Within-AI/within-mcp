# Current State Workflow reference — simple level (Weekly metrics pack refresh)

**Stage:** `current-state`. **Level:** simple. **Primary artifact:** [`current-state.json`](current-state.json), the rendered v2
workflow bundle, alongside [`current-state.svg`](current-state.svg) and the standalone page [`current-state.html`](current-state.html).

## What it shows

The Current State Workflow of this agent in the plugin's v2 workflow format, drawn by `renderer/render_workflow.py`: people's steps in the Analyst band, system results and rails in the System band;
every system is a rail and every read or write is a drop onto it. Files: `current-state.html`, `current-state.input.json`, `current-state.json`, `current-state.svg`. `current-state.input.json` is the
renderer's input, which the output bundle pins as `prior_workflow`.

## Why this level

A pure deterministic script with no model step: it reads one report, writes a Sheet and a Slides review copy and records a run log, while people keep the screenshot and distribution steps. It is the smallest shape an agent can take, so it shows what the stage looks like with nothing extra.

## Final shape a follower reproduces

- **Envelope**: `schema_version: 2`, `kind: workflow`, `variant: current-state`, `process` (`process_ids` equal to the
  snapshot scope), `snapshot` pin (path + SHA-256 of `../../snapshot/simple/snapshot.json`), `presentation: diagram`,
  `diagram` (title, notes), `graph`, `coverage`, `systems_coverage`, and the renderer-written `render`, `render_html`,
  `render_coverage`, `prior_workflow`.
- **Graph**: 18 steps in bands Analyst, System; 3 system rails; 35 edges (read_access 7, sequence 17, write 11); decision tables
  (none: no decision step); `starts`, `outcomes`, `open_ends`; 1 open issues.
- **Evidence**: every node, edge, table, row and rule carries `evidence_state` (Observed, Assumed, Contradictory,
  `needs_more_information`) and `source_refs` pointing at an exact location in the frozen snapshot, or an
  `issue_id` / `issue_ids` naming an open issue.
- **Decision tables**: every decision step names its table; a selection table lists each exit in exactly one row.
- **Coverage**: one row per captured step (18, 18 mapped to graph elements).
- **Systems coverage**: one row per system in the snapshot (3 systems, all mapped onto their rail: Salesforce, Google Slides, Slack).

Checks: `workflow-graph-structure`, `workflow-coverage-manifest`, `workflow-open-and-contradiction`,
`workflow-render-binding` and `workflow-render-geometry` pass; the SVG and HTML redraw byte-identically from the
bundle.

## What is anonymized and how

The client organisation is Acme (the workspace name says so); the Company Brain process id `d42bcd549db7` is kept; every other Company Brain key (workspace, version, hierarchy, task, observation, Companion session) is synthetic in the same shape. People appear only as roles. Commodity tools (Salesforce, Google Slides and Sheets, Slack) keep their names. The graph was converted from the agent's own earlier graph, after anonymizing that graph with the same
substitution ledger as the snapshot, so names agree across the chain.

## Related references at this level

- Company Brain Snapshot: [`../../snapshot/simple/snapshot-intake.json`](../../snapshot/simple/snapshot-intake.json)
- transformation map: [`../../transformation-plan/simple/transformation-map.md`](../../transformation-plan/simple/transformation-map.md)
- Future State Workflow: [`../../future-state/simple/future-state.json`](../../future-state/simple/future-state.json)
