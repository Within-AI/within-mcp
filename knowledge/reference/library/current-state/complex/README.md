# Current State Workflow reference — complex level (Bank remittance enrichment and posting)

**Stage:** `current-state`. **Level:** complex. **Primary artifact:** [`current-state.json`](current-state.json), the rendered v2
workflow bundle, alongside [`current-state.svg`](current-state.svg) and the standalone page [`current-state.html`](current-state.html).

## What it shows

The Current State Workflow of this agent in the plugin's v2 workflow format, drawn by `renderer/render_workflow.py`: people's steps in the Analyst band, system results and rails in the System band;
every system is a rail and every read or write is a drop onto it. Files: `current-state.html`, `current-state.input.json`, `current-state.json`, `current-state.svg`. `current-state.input.json` is the
renderer's input, which the output bundle pins as `prior_workflow`.

## Why this level

The most complex shape: several agent units with handoffs to the analyst, four decision tables, split steps, a re-proposal loop, contradictory evidence and a long list of open questions. It shows how far the format stretches without special cases.

## Final shape a follower reproduces

- **Envelope**: `schema_version: 2`, `kind: workflow`, `variant: current-state`, `process` (`process_ids` equal to the
  snapshot scope), `snapshot` pin (path + SHA-256 of `../../snapshot/complex/snapshot.json`), `presentation: diagram`,
  `diagram` (title, notes), `graph`, `coverage`, and the renderer-written `render`, `render_html`,
  `render_coverage`, `prior_workflow`.
- **Graph**: 19 steps in bands Analyst, System; 9 system rails; 48 edges (else 2, exception 2, loop 1, read_access 19, sequence 13, then 4, write 7); decision tables
  `dm-source-state-invoice-identity`, `dm-source-state-supporting-evidence`, `dm-allocation-method`, `dm-correction-unresolved`, `dt-n-010`; `starts`, `outcomes`, `open_ends`; 9 open issues.
- **Evidence**: every node, edge, table, row and rule carries `evidence_state` (Observed, Assumed, Contradictory,
  `needs_more_information`) and `source_refs` pointing at an exact location in the frozen snapshot, or an
  `issue_id` / `issue_ids` naming an open issue.
- **Decision tables**: every decision step names its table; a selection table lists each exit in exactly one row.
- **Coverage**: one row per captured step (16, 16 mapped to graph elements).
- **Systems coverage**: `systems_coverage` has one row per system in the snapshot (13, all mapped onto a rail or workspace of the graph). The two ERP names the evidence records (the production instance and the plain name) are two rails.

Checks: `workflow-graph-structure`, `workflow-coverage-manifest`, `workflow-open-and-contradiction`,
`workflow-render-binding` and `workflow-render-geometry` pass; the SVG and HTML redraw byte-identically from the
bundle.

## What is anonymized and how

The client organisation is Acme; the bank the client deals with is Bank and its systems are named by role (Bank portal, Bank statement report); the ERP is ERP (receipts); client-built apps are named by role (Invoicing app, Company records app, Collections workbench); payers are synthetic SYN names; amounts and document numbers are synthetic in the same format; the process id `5bbb985d60a7` (and its dependency `a88919d7bd0c`) is kept, every other Company Brain key is synthetic. A path to another client's material was removed from the legacy future state. The graph was converted from the agent's own earlier graph, after anonymizing that graph with the same
substitution ledger as the snapshot, so names agree across the chain.

## Related references at this level

- Company Brain Snapshot: [`../../snapshot/complex/snapshot-intake.json`](../../snapshot/complex/snapshot-intake.json)
- transformation map: [`../../transformation-plan/complex/transformation-map.md`](../../transformation-plan/complex/transformation-map.md)
- Future State Workflow: [`../../future-state/complex/future-state.json`](../../future-state/complex/future-state.json)
