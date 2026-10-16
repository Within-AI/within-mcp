# Current State Workflow reference — middle level (ERP open-item clearing, read-only agent in the loop)

**Stage:** `current-state`. **Level:** middle. **Primary artifact:** [`current-state.json`](current-state.json), the rendered v2
workflow bundle, alongside [`current-state.svg`](current-state.svg) and the standalone page [`current-state.html`](current-state.html).

## What it shows

The Current State Workflow of this agent in the plugin's v2 workflow format, drawn by `renderer/render_workflow.py`: people's steps in the Analyst band, system results and rails in the System band;
every system is a rail and every read or write is a drop onto it. Files: `current-state.html`, `current-state.input.json`, `current-state.json`, `current-state.svg`. `current-state.input.json` is the
renderer's input, which the output bundle pins as `prior_workflow`.

## Why this level

A middle-complexity agent: a scheduled read-only agent groups open items and briefs the analyst, and a second, analyst-started exception seam triages the clearing log. Two judgment units, no agent writes, several decision points and loops in the current state.

## Final shape a follower reproduces

- **Envelope**: `schema_version: 2`, `kind: workflow`, `variant: current-state`, `process` (`process_ids` equal to the
  snapshot scope), `snapshot` pin (path + SHA-256 of `../../snapshot/middle/snapshot.json`), `presentation: diagram`,
  `diagram` (title, notes), `graph`, `coverage`, and the renderer-written `render`, `render_html`,
  `render_coverage`, `prior_workflow`.
- **Graph**: 23 steps in bands Analyst, System; 4 system rails; 46 edges (else 3, loop 3, read_access 11, sequence 17, then 5, write 7); decision tables
  `dm-signal-label`, `dt-n-dec-period`, `dt-n-dec-exceptions`, `dt-n-dec-allocation`, `dt-n-dec-more-scope`; `starts`, `outcomes`, `open_ends`; 13 open issues.
- **Systems coverage**: `systems_coverage` has one row per system in the snapshot's inventory (6): five `mapped` onto the
  graph (ERP (clearing) and Microsoft Outlook as rails, Microsoft Excel, Sticky Notes and Adobe Acrobat Reader as workspaces) and one `cut` with its reason
  (ERP (clearing) web client, named on one step and carried by the ERP (clearing) rail). Check: `workflow-systems-coverage`.
- **Evidence**: every node, edge, table, row and rule carries `evidence_state` (Observed, Assumed, Contradictory,
  `needs_more_information`) and `source_refs` pointing at an exact location in the frozen snapshot, or an
  `issue_id` / `issue_ids` naming an open issue.
- **Decision tables**: every decision step names its table; a selection table lists each exit in exactly one row.
- **Coverage**: one row per captured step (15, 15 mapped to graph elements).

Checks: `workflow-systems-coverage`, `workflow-graph-structure`, `workflow-coverage-manifest`, `workflow-open-and-contradiction`,
`workflow-render-binding` and `workflow-render-geometry` pass; the SVG and HTML redraw byte-identically from the
bundle.

## What is anonymized and how

The client organisation is Acme; the ERP is ERP (clearing), its programs and apps are named by role (ERP clearing program, ERP line-item app); company codes are AC codes; clearing and posting-period variant names, places and the downstream client entity are neutral labels; amounts, G/L accounts, assignment and document numbers are synthetic in the same format; the process id `ac4ea12e7a66` (and its dependency `c0a6b3a6d555`) is kept, every other Company Brain key is synthetic. Commodity tools (Excel, Outlook, Teams) keep their names. The graph was converted from the agent's own earlier graph, after anonymizing that graph with the same
substitution ledger as the snapshot, so names agree across the chain.

## Related references at this level

- Company Brain Snapshot: [`../../snapshot/middle/snapshot-intake.json`](../../snapshot/middle/snapshot-intake.json)
- transformation map: [`../../transformation-plan/middle/transformation-map.md`](../../transformation-plan/middle/transformation-map.md)
- Future State Workflow: [`../../future-state/middle/future-state.json`](../../future-state/middle/future-state.json)
