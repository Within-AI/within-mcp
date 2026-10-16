# Deterministic workflow diagrams

Current state (stage 2) and future state (stage 4) use the **same** band renderer,
`renderer/render_workflow.py`, and `templates/workflow-diagram.html`. The diagram has
one band per performer: **Analyst** (people's steps), **Agent** (the agent's steps) and
**System** (system rails and results), plus a band for any other lane the map uses.
Every system is one full-width rail in the System band; reads are dashed drops onto
it and writes are solid drops (dark coral when the agent writes). Handoffs are coral.
Decisions are diamonds, each linked to its decision table. The graph supplies all
meaning; the renderer never infers owners, adds steps, deletes cycles, or resolves an
open issue. Actual workflows can contain cycles; the renderer draws them.

## Format

A workflow bundle is JSON with schema version 2. One format serves both states; the
current state simply omits the agent-only parts.

### Envelope

| Field | Rule |
| --- | --- |
| `schema_version` | `2`. Version-1 bundles are rejected and need re-authoring in v2 |
| `kind` | `"workflow"` |
| `variant` | `"current-state"` or `"future-state"`; any other string gets the general rules and a note |
| `process` | `{name, process_ids}`; `process_ids` equals the pinned snapshot's scope |
| `snapshot` | `{path, sha256}` of the validated frozen snapshot |
| `prior_workflow` | optional `{path, sha256}`; the renderer sets it to the render input |
| `current_state` | **required on every future-state bundle** (below); ignored on a current state |
| `presentation` | `"diagram"` or `"none"` |
| `diagram` | optional `{title, subtitle, notes, band_labels}`; title defaults to `process.name`. `band_labels` changes only the drawn band text, e.g. `{"Agent": "Script"}` |
| `graph` | nodes, edges, workspaces, decision tables, starts, outcomes, open ends, issues |
| `coverage` | one row per captured step: `{step_id, mapping, graph_ids, rationale?, issue_id?}`, `mapping` is `mapped`, `summary` or `open` |
| `systems_coverage` | one row per system in the snapshot's systems inventory: `{system, treatment, rationale?}`, `treatment` is `mapped` (some node's `systems` names a rail whose `title`, or a workspace whose `name`, is that system) or `cut` (with a rationale); not needed when the snapshot has no systems inventory. See `deterministic-preflight.md` |
| `render`, `render_html`, `render_coverage` | written by the renderer |
| `reference_use`, any other key | accepted and ignored |

### The future state's link to its current state

Company Brain establishes the current state, so a current state always exists. Every
future-state bundle pins the current state it transforms, in whatever shape that
current state exists:

```json
"current_state": {"path": "<file, relative to this bundle>", "sha256": "<its SHA-256>",
                  "kind": "workflow-bundle | <any other non-empty string>",
                  "carried_open_items": [{"id": "co-1", "text": "<as the file states it>",
                                          "source_locator": "<where in the file>", "issue_id": "<open issue>"}]}
```

| Shape | Selected by | Checked |
| --- | --- | --- |
| Plugin current-state bundle | `kind` absent or `"workflow-bundle"` | by script: the file is a v2 current-state bundle that passes the graph rules and pins the same snapshot; every current issue is carried unchanged; a dropped or changed decision table is noted |
| Any other shape (a brought-in diagram, a bespoke graph, a document) | any other `kind`, shown verbatim | the file is resolved and hash-checked but never parsed; `carried_open_items` lists every open question, gap or contradiction carried forward, each bound to an open issue of the future graph; the stage's review checks the list against the pinned file. The page notes "Current state in a different shape; carried items checked by review." |

A missing link fails as broken input; no shape of current state is refused. List the
pinned file among the future-state stage's `inputs` so it is in the review set.

### Nodes

Every node has `id`, `type`, `title`, `lane`, `evidence_state` and `source_refs`
(`issue_id` when unsourced, Contradictory or `needs_more_information`; an element with
no source is never Observed, even with an open issue, so a design addition is Assumed). Optional:
`performer`, `detail`, `facts[{label, text}]` (tooltip), `annotation` (pill under the
box), `treatment` (future state), `systems` (rail or workspace ids),
`decision_table`. Known types, drawn specially:

| `type` | Usual lane | Drawn as |
| --- | --- | --- |
| `read_interaction`, `write_interaction`, `analyst_reconciliation`, `analyst_transfer` | Analyst | box tagged READ / WRITE / RECONCILE / TRANSFER |
| `decision` | Analyst or Agent | diamond; needs `decision_table`; an Agent-band decision is tagged "agent decides" |
| `agent_read`, `agent_reconciliation`, `agent_stage` | Agent | box tagged READ / RECOMMEND / STAGE (`stages_to` pill) |
| `system_surface` | System | full-width rail with `short_label`, `surface_kind`, `access_state` |
| `system_result` | System | dashed result box |

Any other System-lane node is a system-performed step (a nightly job, a bot), drawn in
the System band's step row above the results. Known treatments: `person-kept`,
`agent-taken`, `split`, `system-surface`.

### Edges

`id`, `kind`, `source`, `target`, evidence as nodes; optional `condition`, `branch`,
`label` (tooltip) and `display_label` (drawn, 32 characters). Known kinds:
`sequence`, `then`, `else` (control flow), `exception`, `loop`, `handoff`, `inbound`
(a person hands work to the agent), `read_access`, `write`. The routing follows the
endpoints, not the kind: an edge to a rail is a drop, from a rail a rise, between two
steps a step link.

### Decision tables

`graph.decision_tables[]`: `{id, owner, kind, purpose, columns, rows, cells, rules,
evidence_state, source_refs, issue_ids}`. `owner` is a node id or a list; the owner and
the nodes that name the table mirror each other. `consultation` tables hold a complete
row × column grid of outcomes; `selection` rows lead to an outgoing edge of an owner,
and every branch of a decision appears in exactly one row (a row left open is
`needs_more_information` with an issue). Other kinds are shown as generic tables.

### Evidence

Every element points at an exact location in the frozen snapshot:
`source_refs: [{source_id, pointer}]` (an included source and a JSON pointer into its
raw pull). Evidence states are Observed, Assumed, Contradictory and
`needs_more_information`. Issues (`graph.issues`) stay `status: "open"`; known kinds
are the intake kinds and `open-question` ("what we still need to know").

### Variant conventions

| | Current state | Future state |
| --- | --- | --- |
| Bands | Analyst, System (+ any other lane; an Agent step is noted) | Analyst, Agent, System (+ any other lane) |
| `treatment` | ignored | shown; noted when absent |
| Rail `access_state` | drawn when present | drawn; noted when absent; Assumed rails are grouped under ACCESS ASSUMED |
| Current-state link | ignored | required |

### Notes

A note is how the checks and the page surface unfamiliar or incomplete content: a
known type outside its usual band, a missing ownership or access state, an orphan
rail, a cycle drawn as a return path, starts not listed, an unreachable step, an
unused band label, a dropped or changed carried decision table, a future state with no
Agent step. The checks print notes after `PASS`; the page marks id-bound notes with a
small i and lists them all. Notes never fail a check.

## Drawing what the renderer has not seen

The plugin never refuses to build an agent because of its shape.

| Unfamiliar thing | Drawn as |
| --- | --- |
| Node type | box with its type as the tag; listed under "Other step types" |
| Lane | its own band, upper-cased label; listed under "Other bands" |
| Link kind | dashed step link or dotted drop with the kind as its label; listed under "Other link kinds" |
| Surface kind | white chip with an outline; kind in the tooltip |
| Treatment | shown verbatim in the tooltip |
| Decision table kind | generic table on the page |
| Issue kind | its own group under "Open items" |

Hard failures exist only where the output would be broken or wrong: an unsupported
schema version, a wrong `kind`, process ids that differ from the snapshot, a missing or
stale pin, duplicate or malformed ids, a missing required field, a value outside the
four evidence states, a dangling link, a node's systems disagreeing with its access
edges, outcomes overlapping open ends, a decision without a table or a table that
disagrees with the drawn branches, an unsourced or unrecorded gap, an Observed element
with no source, a contradiction
naming one source, the agreed `max_nodes` / `max_edges`, incomplete step coverage, a
carried issue that changed or vanished, a missing current-state link, and a render
that no longer matches its graph.

## Render on demand

From this skill's directory, use a valid workflow bundle with its pinned source snapshot:

```sh
python3 renderer/render_workflow.py current.input.json current.bundle.json current.svg
python3 renderer/render_workflow.py future.input.json future.bundle.json future.svg
```

Each command writes SVG, a **standalone HTML** page (`current.html` / `future.html`) and
an output bundle that pins both files and the input. `--html PATH` overrides the HTML
destination. Existing output paths are refused; keep prior versions. The last stdout
line is a JSON receipt with elapsed milliseconds, `model_calls: 0` and the note count;
each note is printed before it as `NOTE render: ...`. Python stdlib only: no Node.js,
npm, diagram service, network or model call.

The standalone page inlines the SVG with pan, zoom, Fit and 100% controls and hover or
focus detail, then lists decision tables, open items, the current-state link,
handoffs, notes and coverage counts. It has no wordmark and no co-brand.

Instantiate `templates/workflow-bundle.template.json`. Run through
`scripts/run-stage.py` in a factory run, retaining the same input/output checks and
event recording. Draft graph meaning once in the context that holds the source. Do not
commission a layout agent, ask an LLM to place coordinates, or re-author SVG after a
mechanical fix. Re-render directly and re-run the affected checks.

## Why this layout

- **Columns are longest-path depth** over the control flow, so left to right is the
  order of the work.
- **Bands are computed** from the lanes in use: people above, the agent in the middle,
  systems below, so a handoff reads as "up to the person" and a read as "down to the
  system".
- **One rail per system** makes every read and write land on a named target.
- **Routing is deterministic and orthogonal**: skips run over corridor tracks, a
  vertical run that would cross another step moves into the column gutter, and labels
  sit on the first free spot of their line.
- **Return paths**: a link that closes a cycle is drawn on a dedicated track below the
  bands, so cycles never break the columns.

## Stage reports and verification

Set `criteria_and_results.visual.kind: diagram` and `embed_path` to the submitted
SVG, relative to `output/inspection/`. The report renderer reads that exact file and
embeds its bytes as image data. The stage 2 and 4 HTML reports therefore retain the
diagram when copied/downloaded alone. Their viewer offers Fit, 100% and zoom controls.
Both stages refuse a missing visual. Completion gates bind the report to the exact
submitted render; a diagram link or Markdown summary is not an embedded report.

The render checks bind every node, edge and decision table to its drawn group (lane,
type, systems, kind and endpoints must equal the graph's), then measure the geometry:
node boxes inside the canvas and apart, orthogonal connectors that avoid other steps
and start and end on their own endpoints, drops that land on their rails, and edge
labels that sit off every step. Finally the SVG and standalone HTML must equal what the
renderer produces from the pinned graph. Checks do not prove semantic truth or
universal visual clarity: density, glyph extents and label-to-label overlap still need
self-view against `visual-standards-checklist.md`. Demo performs that check internally
and keeps any nonblocking limitations visible.

For reproducible synthetic current/future previews and timing:

```sh
python3 tests/test_workflow_diagrams.py --preview-dir /tmp/new-diagram-preview
```

This renders the synthetic Renewal Risk Review example in
`reference/inspection-report-examples/workflow/` (13 steps, five system rails, two
decisions with their tables, a memo-revision loop; the future state adds the Agent
band and a staged memo). It generates both standalone pages and stage inspection
reports; it is synthetic example content, not evidence for a company process.
