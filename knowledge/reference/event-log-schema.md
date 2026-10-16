> **Canonical copy.** This plugin holds the one canonical copy of this document (since 2026-10-01). Change it here, through the plugin's review.

# Factory event log — schema v0.2 (validated against two real de-identified reference runs)

**2026-09-18 additive runtime contract:** see [deterministic-preflight.md](deterministic-preflight.md).
`check.failed` records `{check, artifact_path, target_sha256, argv, exit_code}` without
authorizing anything; `executor.aborted` records `{reason, cycle}`;
`human.review_requested` records `{artifact_path, artifact_sha256, submission_event_id}`.
Measured operations carry `started_at`, `finished_at`, nonnegative `duration_ms`.
Check passes carry writer-measured `reverification_duration_ms`; appended events carry
`writer_validation_duration_ms`. The latter includes re-verification. Missing timing
is unknown. `report-timings.py` derives the stage cost report without model calls.
Snapshot/workflow executor.started also carries `output_path`, `cycle`, and one
hash-bound primary `inputs` entry. A pre-authored brief declares that output with
`identity_from: executor.submitted`; generated output identity is bound at submission.
Output checks gate review.started before the model invocation. Final human review
follows LLM clearance and binds submission_event_id plus artifact_sha256. Diagram
submissions are versioned JSON bundles pinning the SVG, not unbound rendered files.

**Demo review exception:** `review.returned` records the existing worker's
self-assessment with `payload.assessment_kind: demo-self-assessment`. Its actor is
the executor using its existing engine, or the current host (`orchestrator`, actual
model engine) for scripted artifacts. No `review.started` or cross-model call occurs.
The writer records `demo_acceptance.independent_review: false`, preserves the raw
scores, and still binds the assessment to checked artifact bytes. This exception is
rejected outside Demo. See [demo-mode.md](demo-mode.md).

**Demo human-input exception:** routine stages require no human acceptance event.
`human.review_requested` additionally requires a concrete `reason` and a
`reason_category` from `access`, `scope-change`, `authority`, `company-control`, or
`coherence-blocker`, bound to the current submission. It pauses clearance until a
real human decision after that request. Explicit `run.opened.payload.human_review_required`
controls remain effective; describe the control in `human_review_reason` and do not
set this flag as a Demo default. A user rejection or correction remains effective.
Automatic progress must never be recorded as fabricated human acceptance.

The append-only typed event log is the factory's **single continuity source** (principle 6). Every
durable view — the baton, the current-state view, the handoff view — is *derived* from it, never authored
independently. This replaces the prose baton earlier runs relied on, and it is the hinge the watcher reacts to
(principle 14 + automation posture).

**Status:** v0.2 — the taxonomy was **replayed against a real de-identified reference run**.
That replay surfaced 5 transitions v0 (happy-path) could not represent — forks, parallel-review
reconciliation, stalled attempts, brief retrofits, mid-execution human steers, late feasibility shelving.
Those are folded in below (marked **[v0.1]**). Serialization, granularity, and run-folder layout are
confirmed (JSON-lines, fine grain, `run/` folder).

## Serialization (proposed)

**One append-only JSON-lines file per run:** `clients/<client>/agents/<agent>/run/events.jsonl`.
One event object per line, append-only, never edited in place. Rationale: trivially appendable from any
process (Claude, Codex, a shell script), tailable by a watcher, git-diffable, and order-preserving. A
run is the unit; the file is its spine.

**Run header (first line of the file) [v0.1]:** `{type: "run.opened", run_id, client, agent, parent_run,
fork_reason, loop_policy: {max_cycles: 2, on_exit: "escalate"}}` — `parent_run`/`fork_reason` capture run
lineage when a run forks (a de-identified reference run's V2→V3 fork); `loop_policy` is the default remediation-loop exit rule,
overridable per stage (see "Loop model").

## Event envelope (every event)

```json
{
  "event_id": "evt-0001",            // monotonic per run, zero-padded
  "ts": "2026-08-26T12:00:00Z",      // stamped by the writer at append time
  "run_id": "<run-slug>-v1",   // the run this belongs to
  "stage": "gate1-mental-model",     // which stage/gate (or "stage-0a", "stage-0b")
  "type": "review.returned",         // from the taxonomy below
  "actor": {                          // who emitted it
    "role": "reviewer",              // orchestrator | executor | reviewer | human | watcher
    "engine": "codex",               // claude | codex | human | script
    "model_config": "codex-…"        // model/version where applicable; null for human/script
  },
  "payload": { … }                    // type-specific, below
}
```

## Event taxonomy + payloads

Stage 0 (front-load + gate):
- `snapshot.frozen` — payload: `{source: "company-brain"|"fde-vocab", path, sha256, pulled_at, note}`.
  Records each front-loaded capture. After the company-brain one, "no re-pull" is in force.
- `feasibility.gated` — payload: `{surfaces: [{name, access: "yes"|"no"|"partial", evidence}], verdict: "proceed"|"stop"|"rescope", rationale}`. **[v0.1] fires at ANY stage, not only Stage 0b** — Stage 0b's job is to make it fire *early*; the log must still represent it firing late (a de-identified reference run: a stop on a client system whose read access was blocked surfaced mid-run).
- `scope.shelved` **[v0.1]** — payload: `{what, reason, evidence, kept_standalone: [...]}`. A feasibility stop that shelves a deliverable while keeping other artifacts (a de-identified reference run: a deliverable was shelved while other artifacts were kept standalone).

Failure-mode lifecycle [v0.2]:
- `failure_mode.logged` — payload: `{failure_mode_id, family, artifact_vs_machinery: "artifact"|"machinery", evidence_events, preventive_control, verification_status}`. Opens the named failure family and records the first control hypothesis.
- `failure_mode.opened` — payload: `{failure_mode: {id} | failure_mode_id, classification, evidence_events, containment, preventive_control, verification_status}`. Records an occurrence in the typed lifecycle. The identifier must resolve from either permitted shape.
- `failure_mode.verified` — payload: `{failure_mode: {id} | failure_mode_id, status, verification, evidence_events?}`. Records evidence that a control has been demonstrated.
- `failure_mode.closed` — payload: `{failure_mode: {id} | failure_mode_id, verification, verification_event?, next_authorized_action?}`. Closes the failure family. Derived views fold these events by id with last-write-wins status; they are not independently authored or identity-pinned artifacts.

Authoring (orchestrator):
- `packet.authored` — payload: `{packet_path, sha256, checklist_pass: {rubric, return_path, reproducibility_bar, fidelity_filter, geometry, authority, invariants, judgment_model}, inputs: [{path, sha256}]}`. The brief-completeness checklist is recorded as *machine-checked booleans*, so "enforced at authoring, not remembered" is verifiable.
- `brief.authored` — payload: `{brief_path, sha256, return_path_named: true, anchored_rubric: true, criteria: [ids], supersedes: <prior_brief_sha|null>}`. **[v0.1] `supersedes`** covers the anchored-rubric retrofit / any re-authoring; the prior return is retained as failure corpus.

Execution (executor):
- `executor.started` — payload: `{packet_path, inputs: [{path, sha256}]}`.
- `executor.submitted` — payload: `{artifact_path, sha256, mtime, self_checks: {…}}`. Triggers the watcher.

Review (reviewer — cross-engine, Codex):
- `review.started` — payload: `{brief_path, artifact_path, artifact_sha256}` (identity verified first).
- `review.returned` — payload: `{return_path, disposition: "clear"|"remediate"|"escalate", scores: [{criterion, score, critical}], findings: […]}`. Parallel independent reviews of one artifact emit multiple of these on the same `artifact_sha256`.
- `review.aborted` **[v0.1]** — payload: `{reason, not_a_finding: true}`. A review attempt that could not file (tooling / missing return path — a de-identified reference run's Gate 1 first pass). The brief-completeness checklist (`return_path_named`) is the prevention; this keeps the log honest that an attempt happened.
- `review.reconciled` **[v0.1]** — payload: `{reviews: [event_ids], resolved_disposition, rationale}`. The orchestrator's neutral reconciliation of one-or-many `review.returned` into the governing verdict — makes the parallel-review catch (and which verdict wins, and why) first-class. Also carries a schema-defect reconciliation (a de-identified reference run's V2 Gate 0 escalate → schema defect).

Loop control — a numbered cycle sequence per stage, **not** a fixed review+one-re-review shape:
- Every loop event carries a **`cycle`** index (1, 2, 3, …). A stage's loop is `cycle 1` (initial review)
  → if not `clear`, `remediation.authored` → `cycle 2` (re-review) → … The cap is a **policy, not a
  structural limit** (see "Loop model" below) — the log can hold cycle 3+ if the policy allows it.
- `remediation.authored` — payload: `{cycle, scoped_to: [criterion_ids], packet_path, no_regression_guard: [criterion_ids]}`.
- `artifact.changed_verified` — payload: `{cycle, path, old_sha256, new_sha256}`. Guards the "remediation silently didn't land" failure.
- `stage.cleared` / `stage.blocked` — payload: `{cycle, disposition, at_criterion?}`.
  - **Gated authorization [v0.2].** A `stage.cleared` for a registered stage requires, on top of the governing latest-submission-bound review-clear, the stage's re-verified mechanical check set bound to the latest submitted SHA — and, for a diagram-producing stage, an `accepted` `human.visual_review` on that same render SHA. Passing review is necessary, not sufficient. The stage→required-set registry (`STAGE_CLEARED_CHECKS` in `append-event.sh`) is keyed on the real stage tags; a stage absent from the registry clears exactly as before (governing review-clear only), so the binding is additive. **[2026-09-25] `requires_gate`:** a registry entry may also name a gate that must be current at clearance (`eval` and `pre-build-simulation` require `design-coherence`); the writer derives it (`scripts/_coherence_gate.py`) and refuses the terminal event with each reason when it is not current. A registered stage whose check scripts or review floor are missing from an installation is refused, naming the missing piece, never cleared as if it had nothing to check. **[2026-09-28] `requires_review`:** stage 11's entry also names its business-owner review floor (`scripts/_prebuild_review.py`): at `stage.cleared` and `stage.accepted_with_residual` it re-hashes the submitted manifest and every file it pins, and in a Project run requires the business owner's accepted review of that exact submission (the decision's payload rules: `pre-build-simulation-stage/templates/prebuild-review-decision.template.yaml`).
- `stage.reopened` **[2026-09-25]** — payload: `{reason, trigger: "coherence-finding"|"pre-build-simulation-defect"|"person-request", authorized_by, finding_ref?}`. Lifts a cleared (or otherwise terminal) stage so a routed finding is corrected in the owning artifact, through that stage's own scoped remediation cycle and independent review. **Terminal rule:** a stage is terminal iff its latest terminal event is later than its latest `stage.reopened`. A `stage.reopened` is legal only when the stage is terminal (reopening an open stage is refused), only for a canonical stage key (never a gate tag or a legacy key), and only when `authorized_by` names an accepted `human.decision` already in the log, or is the literal `"fast-mode-policy"` on a run whose `run.opened.payload` has `mode: "factory"` and `fast_mode: true`. `finding_ref` (a findings-register or defect id) is required unless the trigger is `person-request`. The reopened stage's next submission is the one its reviews and clearance bind to; the resume read shows it as not done and next actionable.
- `stage.accepted_with_residual` **[v0.2]** — payload: `{cycle, disposition: "accept-with-open", gate_ref, residuals: [{id, kind: "value"|"rule"|"scope"|"access", hydrate_at}]}`. The terminal "accept-with-open" disposition (G-F, "Sign off the stage"): a materially-complete artifact landed with named residuals carried honestly by kind rather than buried in a note. It is **human-gated**: `gate_ref` is **required** and must reference an accepted G-F `human.decision` (gate `acceptance` / `current-state-acceptance`, decision `accepted`) for the stage — a review-clear plus checks alone may **not** land an accept-with-open. On top of that human sign-off it rides the **same** governing latest-submission-bound review-clear and the same required-check floor (and, for a diagram stage, the `accepted` `human.visual_review`) as `stage.cleared` — `append-event.sh` enforces the parity plus the required `gate_ref`; it differs from `stage.cleared` in `disposition == "accept-with-open"`, the required `gate_ref`, and the non-empty `residuals[]`. A terminal event: once recorded, no further same-stage event may be appended.
- `escalated` — payload: `{stage, cycle, reason: "loop-exit"|"no-progress"|"structurally-incapable-gate", cycles_used, max_cycles, to: "human"}`. Fires when the loop-exit policy trips (the default is the two-cycle rule) — **or earlier** on the Orchestrator-Standard grounds (a remediation that made no progress, or a gate structurally unable to catch the defect — a de-identified reference run's two stopped visual-gate loops).

Run structure [v0.1]:
- `schema.revised` — payload: `{from_version, to_version, delta, ratified_by, reconciled_from: [review_event_ids]}`. A review escalate reconciled as a schema defect, human-ratified into a fix, forking the run (a de-identified reference run's V2→V3). Pairs with the run header's `parent_run`/`fork_reason`.

Human gates (irreducible):
- `human.decision` — payload: `{gate: "boundary"|"current-state-acceptance"|"future-state-remit"|"acceptance"|"feasibility"|"mid-execution-steer"|"intake"|"brief-approval"|"entry-confirm"|"advisory-approval"|"prebuild-simulation-review"|"provisional-values", decision: "accepted"|"rejected"|"changes-requested", rationale, what_changed?, implies_brief_defect?}`. **[v0.1] `mid-execution-steer`** logs a live human intervention mid-build AND, via `implies_brief_defect`, flags the packet as under-specified evidence. **[v0.2] `intake`** ("Confirm what we're building", G-A) and **`brief-approval`** ("Approve the stage plan", G-B)** extend the gate enum for the two front gates; the `gate` and `decision` values are now enum-checked by the writer (previously key-presence only) so an invented gate/decision is refused. **[v0.2] `entry-confirm`** ("Confirm where to start", §3.6) and **`advisory-approval`** ("Approve what to enrich", §3.6) extend the gate enum for the two front-door touchpoints; both are enforced by the writer (`HUMAN_GATE_VALUES` in `scripts/_schema.py`). **[2026-09-25] `prebuild-simulation-review`** (the business owner's review of stage 11's samples and outcomes, recorded from the simulation page's downloaded feedback file) and **`provisional-values`** (a person confirming the provisional test values stage 11 uses for open thresholds) extend the enum the same way. **[2026-09-28]** Each of these two carries a written payload: `pre-build-simulation-stage/templates/prebuild-review-decision.template.yaml` and `pre-build-simulation-stage/templates/provisional-values-confirmation.template.yaml`; the writer checks every rule of the list before it appends the decision and refuses it, naming each broken rule. The gate-conditional exception payload at `mid-execution-steer` (G-E, "Resolve a flagged blocker") carries `trigger ∈ {non-convergence, unbindable-predicate, coherence-residual, eval-instrument-failure, uncovered-escalation-trigger}` (the five-value Correction-B enum, matching the companion doc).
- `human.visual_review` **[v0.2]** — payload: `{stage, artifact_path, artifact_sha256, checklist: [{item, pass}], decision: "accepted"|"changes-requested", rationale}`. The typed home for the render accuracy gate ("Check the diagram reads true", G-D), previously prose-only. `artifact_sha256` binds the human judgment to the exact rendered artifact (a changed render re-triggers the gate — the same identity binding `check.passed` uses, but human-authored: `actor.engine == "human"`, not `script`). Its `checklist[]` items report into the BAKED `reference/visual-standards-checklist.md` — this event is the typed binding, it does not redefine the checklist. A diagram-producing stage's `stage.cleared` requires an `accepted` `human.visual_review` on the latest render SHA (see "Gated authorization" below).

**Gate tags [2026-09-25].** A gate tag is a `stage` value that is not a pipeline stage. The one gate is
`design-coherence`, the design-coherence gate that closes stage 10 (`scripts/_stageindex.py` `GATE_TAGS`). Its
executor, checks and review events carry the tag; its pass is derived from its three checks, its review and the
design set on disk (`scripts/_coherence_gate.py`), never recorded: `stage.cleared`, `stage.accepted_with_residual`
and `stage.blocked` are refused on a gate tag, and a gate tag is never terminal. `coherence-loop` appears in logs
authored before 2026-09-25; new runs never write it (a legacy key, `scripts/_stageindex.py` `LEGACY_KEYS`).

## Loop model — cycle sequence + configurable exit policy

The review/remediation loop is a **numbered sequence of cycles per stage**, governed by a policy — the
two-cycle cap is the *default value of a parameter*, not baked into the structure. This keeps the state
scalable: raise `max_cycles` and the same events represent a 3- or 4-cycle stage with no schema change.

**Policy (run- or stage-level config; on the `run.opened` header, overridable per stage):**
```json
"loop_policy": { "max_cycles": 2, "on_exit": "escalate" }   // default: two cycles then escalate to human
```
- Resolution order for a stage's policy (`scripts/_loop_policy.py`): an explicit stage-scoped
  `loop_policy` on an event in that stage, then a `loop_policy` declared on the stage in
  `assets/shipped-canonical-pipeline.v1.yaml` (today only `coherence-loop`, `max_cycles: 5`), then the
  run header, then the built-in default. Demo still clamps the cap.
- A stage runs `cycle 1 … cycle max_cycles`. If a cycle returns `clear` → `stage.cleared`.
- If `cycle == max_cycles` and still not clear → `escalated{reason:"loop-exit", cycles_used, max_cycles}`
  to the human owner (the automation posture's "second rejection escalates"; the Standard's loop-exit rule).
- Early exit before the cap: `escalated{reason:"no-progress"}` (a remediation didn't change the verdict)
  or `escalated{reason:"structurally-incapable-gate"}` (the gate can't catch the defect — hand to human).

**Why a policy, not a hardcode (design owner, 2026-08-26):** A de-identified reference run never needed a third cycle, but the schema
must not *forbid* one. Representing `cycle` as an index + `max_cycles` as config means the escalate tier
is enforced by policy while the state generalizes to N cycles for a harder stage or a future run.

## Derived views (never hand-authored)

- **Current-state view:** fold the log → which packet is active, what has cleared, what is blocked, the
  next authorized action. (Principle: state/handoff views derive from the log.)
- **Handoff view / baton:** fold the log → inputs, unresolved issues, rationale, next owner. Replaces the
  earlier prose baton; a fresh agent reads this, not scrollback.
- Both are pure functions of `events.jsonl` at a point in time.

## What this buys us vs the prose baton

- The watcher has typed events to react to (`executor.submitted` → launch review; `review.returned`
  disposition → advance/remediate/escalate). No event stream in the prose-baton runs → no automation was possible.
- `packet.authored.checklist_pass` makes the Orchestrator Standard *enforced and auditable*, not
  remembered.
- `artifact.changed_verified` + sha/mtime everywhere closes the "remediation didn't land" and
  "wrong-artifact-reviewed" failures mechanically.

## Decisions — resolved

1. **Serialization:** JSON-lines per run. (Pydantic models can validate on top later — principles-doc
   WWSNK item 2; JSONL is the smallest proving slice.) ✅ confirmed.
2. **Event granularity:** fine grain (started/submitted/returned split), **validated** against a de-identified reference run —
   the fine grain is what let the replay map real transitions; coarser would have hidden the
   parallel-review and mid-steer transitions. ✅ confirmed by the replay.
3. **Run-folder layout:** `clients/<client>/agents/<agent>/run/` holding `events.jsonl` + frozen captures
   + packets/briefs/returns. ✅ confirmed.

## Validation status
Replayed against a real de-identified reference run: v0 covered the core author→execute→
review→remediate→re-review→clear→accept loop; the replay surfaced 5 missing transitions (fork, parallel-
review reconciliation, stalled attempt, brief retrofit, mid-execution steer, late feasibility shelving),
all folded in as the **[v0.1]** additions above. The next real run is the next test of
the taxonomy — revise if it exercises a transition v0.1 still can't hold.


## Front-door layer additions (v0.3 — the ATD front door)

Additive over v0.2 and **non-gating**: the front door sits *above* the pipeline and records where a run
starts and what a client-brought artifact carries. Nothing here authorizes a stage clearance or blocks one;
the pipeline's gated-authorization model (v0.2) is unchanged. All additions are enforced by the writer's
typed layer (`scripts/_schema.py`).

New `human.decision.gate` enum values (the two front-door gates; the flat `{gate, decision, rationale}`
shape is reused, plus a gate-conditional payload):
- **`entry-confirm`** — "Confirm where to start". Extra payload: `entry_classification` ∈
  {resume, enter-with-inputs, fresh}, `recommended_entry_stage`, `recommendation_reason`,
  `selected_entry_classification`, `selected_entry_stage` (the client-selected entry recorded distinctly
  from the recommendation, so an override is preserved).
- **`advisory-approval`** — "Approve what to enrich". Extra payload: `entry_stage`, `advised_enrichments[]`,
  `approved[]`, `declined_gap_ids[]`, `proceed` (always `true`).

New event kinds:
- `graph.authored` — payload `{graph, pipeline_asset, seeded_entry_stage, stages}`. Instantiates the shipped
  canonical pipeline (`assets/shipped-canonical-pipeline.v1.yaml`), seeded at the confirmed entry stage —
  never a bespoke per-run DAG. **Seeded-root / upstream-deps encoding (resolved at integration):**
  each `stages[]` entry is `{name, active, seeded_root, deps}`; the seeded stage has `seeded_root: true`,
  stages upstream of it are `active: false`, and each active stage's `deps` names the previous active stage.
  The resume read derives the run's start from `active` + `seeded_root`.
- `handoff.internal` — payload `{to_stage, provenance, artifact_object_kind?}`. The internal-handoff join
  marker: the reviewed future-state graph handed to the design half on the `fresh` path (`provenance`
  same-run), or a client-brought artifact satisfying the seeded stage's consumed input on the
  enter-with-inputs path (`provenance: client-brought`), so the resume read treats the seeded stage's
  upstream dep as satisfied.
- `sufficiency.assessed` — payload `{entry_stage, object_kind, manifest_version, artifact_sha256, results:
  [{item_id, verdict ∈ {present, thin, absent}, coverage_fraction}]}`. Script-computed coverage of a
  client-brought artifact against the shipped `assets/required-input-manifest.yaml` at the bound version.
  `actor.engine == script` is **enforced** (writer-re-derived). **Non-gating** — authorizes nothing, blocks
  nothing; "thin"/"absent" are localized coverage facts, not a verdict.
- `heal.applied` — payload `{entry_stage, source_version, result_version, approved_gap_ids, proceed, …}`.
  The consented advisory auto-heal record; consent is the preceding `human.decision` (gate ==
  advisory-approval). Non-gating.
- `artifact.version` — payload `{artifact_path, version, supersedes, …}`. Records a healed artifact version
  as a first-class versioned artifact (never a silent in-place mutation of the brought v0). Non-gating.

Stage-definition field (not an event): entry-point stage definitions in the shipped canonical pipeline
carry an additive, optional **`entry_input_declaration`** block (shape:
`assets/entry-input-declaration.schema.yaml`) — `{consumed_input, client_brought_satisfiable, object_kinds,
manifest_ref:{manifest_version, stage_key}}`. It is metadata the front door reads to map a client-brought
object kind to a seeded entry stage and bind that stage to a required-input-manifest version; it reworks no
stage's own logic. Carried only by the entry-point stages (transformation, backbone); internal stages carry
none.
