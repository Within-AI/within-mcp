# Deterministic preflight and increasing-cost review

Contract version: 1. Adopted 2026-09-18 at the owner's request to reduce avoidable
generation/review cycles and standardize snapshot and workflow validation. This
implements the charter's escalating review cost constraint. It changes the former
human-first diagram review order; existing historical logs/examples are not rewritten.

The cycle is: deterministic input preflight → generation → deterministic output
preflight → independent LLM review → required human review. Every revision repeats
the applicable checks against the new input/output identities. Scope, access and
authority decisions needed to authorize generation still happen beforehand.

In Demo, replace independent LLM review with the existing executor's first-pass
self-assessment (`demo-mode.md`). No second model or reviewer CLI is invoked.
Output checks still apply. Demo pauses only for significant human decisions or
explicit company controls, not routine artifact acceptance; the controller returns
`await-self-assessment` and the writer records the assessment without a model call.

## What runs where

| Artifact | Before generation | Before LLM review |
|---|---|---|
| Snapshot | `snapshot-intake`: raw file hashes, process membership (pointer compared, or the source's stated id), mapped shape, cutoff, source inventory, essential availability and pagination | `snapshot-structure`: rederive steps, profiles, scope, gaps and source inventory from the pinned intake |
| Current-state graph | `snapshot-structure` | `workflow-graph-structure`, `workflow-coverage-manifest`, `workflow-systems-coverage`, `workflow-open-and-contradiction` |
| Workflow diagram | `workflow-graph-structure` | Graph checks plus `workflow-render-binding` and `workflow-render-geometry` |
| Existing design stages | Their declared entry contract | Their existing registered artifact checks now also gate `review.started`/`review.returned` |

`scripts/_stage_checks.py` is the shared snapshot/workflow policy used by the writer,
launcher and controller. It covers the canonical snapshot/current-state/future-state
keys and the legacy graph/render stage keys. It does not silently turn the advisory
client-entry coverage helper into a hard gate. A brought artifact needs an explicit
adapter into this versioned internal contract before a generated artifact can use it.

## Source contract

Start from `templates/snapshot-intake.template.json` and
`templates/workflow-bundle.template.json`. They are intentionally incomplete and fail
checks until populated from real evidence. The workflow template is the graph input
to rendering; Project/Demo submissions must use the rendered output bundle.

Preserve raw MCP results as JSON. The intake is a separate JSON object with:

```json
{
  "schema_version": 1,
  "kind": "snapshot-intake",
  "scope": {"process_ids": ["process-1"], "cutoff_at": "2026-09-18T00:00:00Z"},
  "required_source_ids": ["process-details"],
  "sources": [{
    "id": "process-details", "process_id": "process-1", "status": "included",
    "path": "raw/process.json", "sha256": "<actual SHA-256>",
    "mapping": {"process_id": "/resource_key", "steps": "/steps", "step_id": "/code"}
  }],
  "issues": []
}
```

Mappings are JSON pointers into the observed raw shape, not an assumed Company Brain
schema. Every source states its `process_id`, which must be in the scope's
`process_ids`. When the raw pull carries its own process id, the `process_id` pointer
names it and the check compares the two values. A pull that carries none (a collection
such as the observations list, which returns only items and paging fields) omits the
pointer: the source's stated `process_id` is accepted as its binding, with no reason
note. Supply `items`, `total` and, where available, `next` pointers for collections;
`timestamp` checks dated evidence against the cutoff. A `steps` pointer names one
actual list of steps and `step_id` is relative to each step. Nested task/subtask trees
can be represented by separate source rows pointing to each real list in the same
raw file. Namespaced step IDs are `source-id:source-step-id`.

A `systems` pointer names a source's process-level list of system names (for example
`systems_used`), and a `step_tools` pointer, relative to each step, names that step's
tool list. Both are optional, but the snapshot's systems inventory is the union of what
they resolve to, so a source that records systems needs them. Every entry must be a
non-empty name; anything else is refused rather than skipped. A step with no tools field
records no tools, but a `step_tools` pointer that resolves on no step is refused. Names
that differ only in case or spacing are one system.

Missing/inaccessible/excluded sources need a reason and a linked open issue.
Unavailable essential sources and incomplete essential collections block generation.
Optional partial pulls need an explicit coverage issue. An issue is
`{id, kind, status: "open", reason, source_ids}`; kinds are missing, inaccessible,
contradiction, policy, coverage. Contradictions reference both source claims (separate
source rows may point into the same raw file). Declaring source scope, required
evidence and interpreting contradictions remain judgment tasks. Code validates those
declarations; it does not discover missing sources or prove the evidence true.

Run `python3 scripts/freeze-snapshot.py INTAKE.json SNAPSHOT.json`. It creates a new
snapshot, derives inventory/step IDs and a field/type/step-count profile from raw
pulls, and pins the intake. It never overwrites a snapshot. Raw source paths in the
snapshot remain relative to its referenced intake, avoiding copied data and drift.
The existing `extract-process-facts.py` still consumes the original raw-pulls bundle;
do not feed the normalized snapshot envelope into that extractor.

## Workflow format and rendering

The submitted JSON bundle has `schema_version: 2`, `kind: "workflow"`, a `variant`
(`current-state` or `future-state`), a `process` whose ids match the snapshot scope, a
`snapshot` path/SHA reference, `graph`, `coverage`, and `presentation: "diagram" | "none"`.
The graph carries typed nodes in bands (Analyst, Agent, System, or any other lane),
system rails (`system_surface` nodes), edges including `read_access` and `write`
access edges, decision tables linked from decision steps, starts, outcomes,
`open_ends` and issues. Every element has a unique ID, an evidence state and
`source_refs` (`[{source_id, pointer}]`) or a linked open issue; a node's `systems`
and its access edges must agree. Unfamiliar node types, lanes, link kinds and other
values are drawn generically and pass; notes point at them. The current-state link is
required on every future-state bundle, in either shape: a plugin current-state bundle
(carried by script: its issues unchanged, its decision tables noted when dropped or
changed) or a current state in another shape with `kind` and `carried_open_items`,
checked by review. The full format is in `workflow-diagrams.md`, Format. Optional
agreed max_nodes/max_edges live in the pinned intake scope's limits, never in the
generated graph's own self-assessment.

Each coverage row is `{step_id, mapping, graph_ids}`. Every source step occurs
exactly once; one step may map to several graph elements (nodes, edges, decision
tables or table rows) and several steps may map to one node. `summary` and `open`
mappings need a rationale; `open` also needs an issue_id. Code checks completeness and
references; an LLM judges whether grouping or summarization preserves meaning. Source
issues are carried without alteration. Resolving an issue requires a newly ratified
input version; generation cannot silently resolve an inherited open. An optional
`prior_workflow` path/SHA also binds carried issues. A future-state bundle must also
pin its current state in `current_state` (see `workflow-diagrams.md`, Format).

Each `systems_coverage` row is `{system, treatment, rationale?}`. Every system in the
snapshot inventory occurs exactly once, matched regardless of case and spacing.
`mapped` means the system is on at least one node's `systems`; `cut` means it is on none
and needs a rationale. A node's `systems` holds rail or workspace ids, so a system is on a
node when that node names a system rail whose `title` (or id), or a workspace whose `name`
(or id), is the system's name. An operational system cannot be cut with a throwaway reason such
as "n/a" or "not relevant"; agent runtimes and incidental UI tools may be cut with a
short one. Failures name the systems at fault. A snapshot with no systems inventory needs
no ledger. Current-state and future-state graphs both carry the ledger; whether a keep or
cut was wise is the reviewer's call.

Run `python3 renderer/render_workflow.py INPUT.json OUTPUT.json OUTPUT.svg`. Rendering
is deterministic and emits `data-graph-node-id`, `data-graph-edge-id` and
`data-graph-table-id` groups carrying lane, type, systems, kind, endpoints, bounds and
drawn points, and a complete render_coverage mapping. The output bundle pins the SVG,
standalone HTML (`OUTPUT.html` by default), and prior input; it is the submission
identity. For legacy render-only stages, the writer also proves the graph is unchanged
from that input. For canonical current-state generation, the generated bundle instead
binds its checked snapshot.

The standard renderer is the band layout: longest-path columns, bands computed from
the lanes in use, one rail per system in the System band, corridor tracks for skips
and cross-band links, a disposition track for return paths and loops, and a column
gutter for any vertical run that would cross another step. It preserves feedback
cycles. It favors inspectability over compactness; dense graphs can produce wide
diagrams. Geometry validation checks node boxes, orthogonal connectors that avoid
other steps and land on their own endpoints, drops that land on their rails and labels
that sit off every step, then compares the actual SVG and HTML with the canonical
projection, preventing forged layout metadata, hidden elements or modified paths from
claiming a pass. It is a conservative geometry floor: browser font rendering, visual
density, routing clarity and semantic label attribution still benefit from inspection
by a person. Demo preserves these limits in its reports without requiring a routine
visual signoff. Custom HTML/BPMN renderers need their own measured geometry adapter and
registered validator; they cannot claim this SVG check passed.

`presentation: "none"` skips render checks explicitly for Factory runs without a
presentation requirement. Project/Demo workflow outputs require diagrams. A bundle
without presentation must not carry an unvalidated render. It does not waive graph checks.

**Validation and migration.** Version-1 workflow bundles are rejected and need
re-authoring in the v2 format; runs that started on version 1 finish on the plugin
version they started on. Snapshot and intake files stay at version 1.

## Launch and acceptance

Author the rubric and packet before generation. Pin existing reference inputs in the
brief's review set.

Use `scripts/prepare-stage-briefs.py` with the compact JSON spec in
`dispatch-policy.md` to generate the packet, rubric, pins and criteria manifest in
one local operation. It runs the input preflight and four release checks, then
records both authored events through the writer. The current host supplies the
semantic instructions/criteria; no drafting agent or model call is involved in the
assembly. It accepts new paths only and leaves failed drafts available for inspection.

For manually assembled briefs, declare the future output with these adjacent lines:

```text
output_path: snapshot.json
identity_from: executor.submitted
```

The review-set check accepts `--pending-output snapshot.json` alongside the existing
input artifacts. It validates their actual hashes; the generated output acquires its
identity from the later submission. This avoids requiring a nonexistent output hash
before generation. The rubric/criteria remain authored first.

After the ordinary `brief.authored` / `packet.authored` events, use:

```sh
python3 scripts/run-stage.py --run-root RUN --stage snapshot \
  --input intake.json --artifact snapshot.json --packet packet.md \
  --engine script --cycle 1 -- \
  python3 /absolute/plugin/knowledge/scripts/freeze-snapshot.py intake.json snapshot.json
```

The generator runs in RUN. Input checks must pass before executor.started; output
checks run after executor.submitted. The writer independently verifies receipts,
source dependencies and output/input binding. `invoke-reviewer.sh` emits review.started
before calling the model, so missing/stale checks stop a paid call, including with
`--force`. The controller emits await-preflight or await-human-review when appropriate.
Final acceptance includes the latest submission_event_id and artifact_sha256. Diagram
reviews use human.visual_review with exactly V1–V6 (the six visual checklist items),
all true for acceptance. Nonvisual acceptance uses human.decision gate acceptance.
A later rejection supersedes an earlier acceptance. Changed outputs require new checks,
LLM review and human acceptance where the mode requires it. Factory mode can omit routine human review when
`run.opened.payload.mode` is `factory` and `human_review_required` is false; company
controls must set the latter true when applicable. Project defaults to human review.
Demo defaults to continuing automatically, including diagram and agent-build stages;
use `demo-mode.md` for significant exceptions. Set `human_review_required: true`
only for an explicit requirement, with its reason, never as a Demo default.

## Measurement

Check passes and failures, generator successes/aborts and reviewer successes/aborts
record started_at, finished_at and monotonic duration_ms. Check receipts also record
writer-measured reverification_duration_ms. Every appended event records total
writer_validation_duration_ms, which includes re-verification and must not be added
to the latter again. `human.review_requested` precedes the human response so wait
time can be derived separately from model time.

`python3 scripts/report-timings.py RUN/events.jsonl` emits JSON by stage: generation,
checks, LLM review, human wait, writer validation, failure/attempt counts and unmeasured
operations. Remediation time is the measured subset of cycles greater than one, not
an extra additive total. Missing measurements are null, never inferred as zero from
synthetic event timestamps. Compare actual runs before claiming time savings. Checks
are reverified against dependency hashes; this version does not cache a pass across
changed source, schema or renderer versions.

## Validation and migration

Run `the workflow preflight test` for source corruption, scope/pagination,
coverage, graph/render tampering, preserved issues, timing, launcher and writer gates.
The canonical stage tags now require these bundles. Existing raw-pull formats and
historical run logs are preserved; continuing an older run requires an explicit
versioned intake/workflow adaptation before these strengthened gates can clear it.
