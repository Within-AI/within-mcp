> **Canonical copy.** This plugin holds the one canonical copy of this document (since 2026-10-01). Change it here, through the plugin's review.

# Workflow-Diagram Factory v1 Experiment

**2026-09-18 owner-approved update:** snapshot and workflow checks now follow the
[deterministic preflight contract](deterministic-preflight.md): input validation →
generation → output validation → LLM review → required human review. This supersedes
the historical human-first render order and adds executable snapshot/graph/render
checks. See that contract for formats, launch commands, timing and migration.

**Status:** Proposed internal experiment.  
**Decision source:** the design owner directly, grill-me session, August 23, 2026.  
**Scope owner:** the design owner.  
**Control:** the existing `workflow-diagram` skill. It remains unchanged throughout this experiment.

## Claim

The first factory slice is not agent scoping or build implementation. It is a bounded, evidence-gated method for turning a frozen Company Brain source package into a reviewed workflow graph, then into a client-presentable **Current State Workflow** and **Future State Workflow** with an agent in the loop. It tests whether a Claude executor and independent Codex reviewer can create those artifacts without manual hand-pasting or manual session launches.

The experiment starts only after an agent opportunity has been selected. It does not replace the existing workflow-diagram skill, and it does not establish the eventual agent build process.

## Intended output

Each pilot produces an operational control-flow view in HTML for both states.

| State | Required view |
| --- | --- |
| Current State Workflow | How the work is observed today: boundaries, actors and systems at material handoffs, decisions, variants, exceptions, loops, residue, and reconvergence. |
| Future State Workflow | The same operational flow after the approved agent role is introduced: retained work, agent work, human work, authority boundaries, and handbacks. |

This view is for process design. It is neither an executive dominant-path summary nor a click-by-click runbook. It may summarize incidental clicks, retries, continuous action, and true same-role surfaces. It must not silently collapse meaningful routes or systems, or hide human actions or agent handbacks.

Executive summaries are out of scope for v1. If needed later, they are a separate view and do not compress the operational diagram in place.

## Semantic model boundary

V1 separates 3 authorities that answer different questions:

| Artifact | Authority | Question it answers |
| --- | --- | --- |
| Frozen Company Brain source package | Raw observed evidence | What was captured, where, and when? |
| Canonical workflow graph `[to confirm]` | Reviewed semantic representation for this run | What are the workflow's nodes, edges, decisions, exceptions, systems, and evidence links? |
| Append-only typed event log | Factory continuity record | What did the executor, reviewer, and human owner do during this run? |

The graph is not a replacement for the source package and the event log is not a replacement for the graph. The graph becomes the accepted semantic input for derived views after its gate clears. The Current State Workflow HTML/BPMN is a rendered view of that graph, not the place where workflow meaning is first invented. It renders from the graph; the mental model is a review reference at its gate, not a second source the view is built from.

The implementation hypothesis is a Pydantic-backed JSON graph. Each node carries a stable ID, kind, label, actor, systems, evidence state, and source references. Each edge carries source and destination IDs, kind, condition or trigger where applicable, and source references. The source package declares the included observation IDs, source-session windows, process-version context, cutoff date, and any excluded material with rationale.

## Artifact chain and gates

The factory has 5 ordered units. A later unit cannot use an artifact until its required gate has cleared.

```text
Frozen Company Brain source package
  └─ Canonical workflow graph
       └─ Codex Gate 0: evidence-to-graph fidelity
       └─ human boundary ratification
            └─ Mental model
                 └─ Codex Gate 1: graph-to-model fidelity
                      └─ Current State Workflow + coverage manifest
                           └─ deterministic output preflight
                           └─ Codex agent structural gate (fidelity to the graph)
                           └─ human visual gate (visually consistent + client-presentable)
                           └─ transformation map
                                └─ human confirmation of agent remit and authority
                                     └─ Future State Workflow
                                          └─ deterministic output preflight
                                          └─ Codex agent structural gate (fidelity to the graph)
                                          └─ human visual gate (visually consistent + client-presentable)
```

| Unit | Narrow mandate | Required output |
| --- | --- | --- |
| 1. Evidence-to-graph | Construct one evidence-linked semantic graph from the frozen source package. | Source inventory, canonical workflow graph in Pydantic-backed JSON, and a node/edge coverage manifest. |
| 2. Mental model | Explain the approved graph's observed process boundary and operating logic. | The existing 7-part mental model: purpose; where the workflow sits; chunks; surfaces; disambiguation of lookalikes; observed deviations; source boundary. |
| 3. Current State Workflow | Render the approved graph as a readable operational control-flow diagram — a deterministic projection of the graph. The mental model is a Gate-2 review cross-check, not a generation input. | Current State Workflow HTML/BPMN and a coverage manifest. |
| 4. Transformation map | Make every present-to-future treatment explicit before a future graph and diagram are rendered. | Mapping of each current node to retained, agent-taken, human-retained, removed-with-reason, or other approved treatment; agent activities mapped to a current antecedent or explicit capability, with authority. |
| 5. Future State Workflow | Apply the approved transformation to the current graph, then render a readable operational control-flow diagram. | Future-state graph diff `[to confirm]`, Future State Workflow HTML/BPMN, and its coverage manifest. |

The graph, mental model, and transformation map are expected to be lower-risk than diagram rendering. They are still independently reviewed because downstream diagram reviews must have a fixed semantic input.

For the per-step classification behind the transformation map's agent-taken treatments — how an
automation pattern is derived from a step's execution mode and review requirement, rather than scored
independently — see `reference/automatability-derivation.md`.

## Work packet

A **work packet** is the atomic execution and review unit. It is a typed record, not a free-form task. Each packet declares:

- stage and narrow mandate;
- locked input artifacts and evidence versions;
- view definition `[to confirm]`: reader, purpose, boundary, must-show material, and permitted summarization;
- expected output schema;
- versioned reference pack and rubric definition;
- executor and independent reviewer;
- pinned model and effort configuration for Claude and Codex;
- maximum attempts and escalation owner; and
- output artifact version and disposition.

The packet resolves model and effort settings when it is created. The local watcher records the configuration actually used; it does not choose models dynamically in v1.

No packet output becomes a downstream input until its Codex gate clears. The builder cannot clear its own work.

## State and evidence handling

V1 uses one authoritative, append-only, typed event log for factory continuity. The implementation hypothesis is Python, Pydantic schemas, and JSONL event files, with derived JSON and Markdown views for current state and handoff. It does not introduce an ActiveGraph runtime or database dependency.

Each event records the actor, input and output artifact versions, disposition, score, critical failures, findings, model configuration, and relevant packet identifier. The event log is the continuity source of truth; state and handoff views are derived from it.

Each work packet pins an immutable versioned reference pack. Rubrics and examples cannot drift during a run. The evidence-to-graph packet also pins its source inventory. The executor cannot silently add an observation, merge a material action, introduce a gateway, or treat an unresolved outcome as complete without recording that choice for review.

The graph package carries a coverage manifest from each material source item to a graph node, edge, or permitted-summary rationale. The diagram package maps each material graph item to a BPMN node or flow, or to a specific permitted-summary rationale. For the Future State Workflow, it maps material transformation-map treatments to the future-state graph and the rendered diagram. The manifests make compression inspectable rather than implicit.

## Review model

Claude executes. Codex reviews independently and decides whether an artifact clears, needs rework, or must escalate. A different author/model family is the intended clearance pattern. Same-family review may be supplementary, but never clears a gate.

The local automation loop is:

```text
Claude execution
  → artifact.submitted event
  → automatic Codex review
  → cleared, or one automatic Claude remediation
  → automatic Codex re-review
  → cleared, or blocked for human escalation
```

The operator should be able to watch this run in a dedicated local terminal or dashboard: Claude output, Codex output and progress, append-only events/current gate, and artifacts/reports. V1 does not promise task injection into this interactive Codex conversation or interactive cloud chat creation.

### Human gates

| Gate | Human decision |
| --- | --- |
| Workflow-graph boundary | Ratify the graph boundary, material contradictions, and unresolved issues after Gate 0. |
| Mental-model boundary | Ratify the model's explanatory boundary after Gate 1. |
| Current State Workflow | After deterministic checks and LLM fidelity review, the human accepts visual consistency and client-presentability. |
| Future-state remit | Confirm agent remit, authority, retained human controls, and exception handback before the transformation map is used. |
| Future State Workflow | After deterministic checks and LLM fidelity review, the human accepts visual consistency and client-presentability. |

Human dispositions are recorded as typed `human.accepted` or `human.rejected` events with rationale.

## Rubric system

V1 uses typed ordinal scores: `0`, `1`, `2`, `3`, `4`, and `5`.

| Score | General meaning |
| --- | --- |
| 0 | The required output does not exist. |
| 1 | Bare-minimum or nominal output. |
| 2–3 | Present but materially inadequate for the criterion. |
| 4 | Passing, fit for the named criterion. |
| 5 | Best attainable output for the named criterion and reference pack. |

A score does not average across criteria. Every required criterion must score at least `4`; a critical failure blocks clearance regardless of other scores. The general scale is only a frame. Every criterion needs its own anchored `0–5` definitions, evidence to inspect, positive and negative references, and a remediation instruction for scores `0–3`.

The rubric definition is a first-class artifact. It must be reviewed and pinned before Claude draws. The rubric is one item on a wider **brief-completeness checklist** every authored brief must satisfy — anchored rubric, named return path, downstream-reproducibility bar, fidelity filter, geometry/layout checklist, authority/scope statement, and invariant-carry-forward on re-authoring. See the [Orchestrator Operating Standard](orchestrator-operating-standard.md).

**Author-before-execute — test-driven order (mandatory for every gate and every iteration).** A gate's review brief (its rubric) must be authored *before* the executor produces the artifact that gate will judge — the same discipline as writing the test before the code. Authoring a rubric after seeing the output risks the rubric being shaped, consciously or not, to fit or to spare the work already done, which destroys the independence the gate exists to provide. This ordering is not optional; it applies to Gate 0, Gates 1–5, and every subsequent iteration of the experiment. Practically: when a stage's executor packet is authored, its gate review brief is authored in the same pass, before the executor runs.

**Iteration-1 deviation (recorded honestly, not to be copied).** In the first iteration of a de-identified reference run this order was violated: the executor produced graph v1 first, and the Gate 0 review brief (kept outside the handoff folder) was authored afterward. Mitigations that limited — but did not eliminate — the contamination risk: the orchestrator did not read the graph JSON itself (only a structural, field-level characterization was used to confirm the criteria were gradeable), and the criteria were grounded in this experiment's principles and the frozen source rather than in the graph's content. Treat this as a known limitation of iteration 1. From Gate 1 onward, and in every later iteration, author each gate's brief before its executor stage runs.

**Iteration-1 result and the wider finding (2026-08-25).** A de-identified reference run completed: the V3 line cleared Track A (current-state graph, mental model, diagram) and Track B (transformation map, future-state graph, mental model, diagram) on anchored instruments. A transcript recon of the run's sessions found that the executors and reviewers performed well against tight, single-purpose, rubric-bearing instruments, while the **orchestrator** was the failure locus — roughly 80% of the run's issues traced to under-specified briefs it authored (missing rubric, missing return path, missing reproducibility bar, missing lane geometry, dropped invariants on re-authoring). The recon also showed an anchored rubric is necessary but not sufficient: two reviewers under the identical rubric split on a register defect. These findings produced the [Orchestrator Operating Standard](orchestrator-operating-standard.md), whose checklist inlines each failure as an enforced item. The full write-up and preserved brief corpus are held internally in a de-identified reference run's retrospective and do not ship.

### Sequential graph and diagram gates

| Gate | Reviewer question | Scope boundary |
| --- | --- | --- |
| Gate 0 — evidence-to-graph fidelity | Does the graph faithfully represent the frozen source package, including source boundary, node and edge evidence, contradictions, unresolved outcomes, and material exceptions? | Tests semantic graph construction, not model prose or diagram quality. |
| Gate 1 — graph-to-model fidelity | Does the mental model faithfully explain the approved graph without adding or hiding material workflow meaning? | Tests the 7-part mental model, not diagram quality. |
| Deterministic output preflight | Are schema, references, coverage, carried issues and graph/render bindings valid? | Script-owned; required before model review. See deterministic-preflight.md. |
| Agent structural gate (fidelity to the graph) | Does the diagram faithfully represent the approved graph (the sole semantic authority) and its coverage manifest, with the mental model as a secondary cross-check? | Codex, on the exact preflight-checked submission. The graph wins over the model. Replaces the former separate Gates 2–5. |
| Human visual gate | Is the rendered diagram legible, visually consistent and client-presentable against the visual checklist? | Human-owned, after LLM clearance. A changed render repeats output preflight, LLM review and human acceptance. |

Current Gate 0 requires, at minimum:

1. A fixed source inventory and source boundary.
2. Every material graph node and edge linked to evidence or a permitted-summary rationale.
3. Explicit treatment of Observed, Assumed, Contradictory, and More information required material.
4. No unsupported gateway, merge, loop, completion state, or owner assignment.
5. Stable node and edge IDs, so downstream diffs and coverage checks are possible.
6. Every system the work touches — the union of each in-scope process's `systems_used` and its per-step tools — accounted for: mapped onto a node, or explicitly cut with a reason (agent runtimes and incidental UI tools still get a row, but a short reason is enough), so no evidence-bearing system is silently dropped (the `workflow-systems-coverage` check).

The agent structural gate (formerly Gate 2) has 7 provisional required criteria:

1. Boundary and outcomes preserved.
2. Operating chunks covered.
3. Material control flow retained: decisions, variants, exceptions, loops, and merges.
4. Material surfaces represented, including system spread.
5. Disambiguations retained.
6. Compression accounted for through the coverage manifest.
7. No unsupported semantics.

The former Gate 3/4/5 concerns — main-path traceability; decision, merge, exception, and loop clarity; connector integrity; spatial hierarchy; system attribution; action-label clarity; and the HTML visual and density bar — are adjudicated at the human visual gate, held to the [visual-standards checklist](visual-standards-checklist.md) as the shipped visual and density reference; its density bar was calibrated against a real de-identified reference render, which is not a semantic or wording template. The agent structural gate's own criteria still require final anchored scoring definitions.

For future-state fidelity, the reviewer compares the approved current graph, Current State Workflow, and transformation map with the future-state graph diff and future diagram. It does not reassess the selected agent scope. The future-state graph carries its own systems-coverage ledger against the same snapshot inventory: a system the future workflow no longer uses is cut with the reason it leaves, so the reviewer can see which systems the agent replaces.

## Reference and failure corpus

Reference packs include good examples, counterexamples, the applicable rubric, and evaluator fixtures. Historical failures are test cases, not background reading. A reviewer is not trusted until it can detect the failure modes in the corpus.

One worked, validator-clean relay ships with the plugin at [`example/`](example/) — event log, executor packet, review brief, submitted artifact, and returned Codex verdict for a single synthetic stage; use it for shape. The reference pack a live run assembles carries the roles below (the role definitions came from a de-identified reference run whose own artifacts are internal and do not ship):

| Role in the reference pack | Use in v1 |
| --- | --- |
| An upstream shortlist-and-sequencing artifact | Upstream opportunity-selection context only; v1 begins after selection. |
| A frozen source process (v25) | Data-shape feasibility reference only: process tree, 29 process-level observations, observation-to-session traceability, and version-history caveat. It is not the pilot selection. |
| A rendered workflow-and-recommendation artifact | Human visual gate: visual/density reference. |
| Company Brain to Agent Technical Design (a later attachment point) | Later attachment point; not in the v1 build scope. |
| A current-state BPMN | Current-state operational control-flow reference. |
| An agent-in-current-workflow BPMN (the agent placed in the real operational flow) | Canonical future/preservation view. |
| An agent-in-loop BPMN that abstracts the flow away | Negative example: abstraction that loses the operational workflow. |
| Workflow-diagram handoffs and reruns | Failure corpus for evaluator fixtures. |

Known failure modes include source coverage that still compresses a process into uncommunicative broad actions; terse labels such as “Triage,” “Locate,” and “Reconcile”; ambiguous decision labels; unsupported corrective actions; line and node collisions; phantom/off-canvas elements; duplicated nodes; recovery content outside the rendered SVG; and validators passing despite diagrams that fail visual review. Automated checks are evidence, not proof. Direct human HTML review remains a gate.

## Pilot and Eval

The pilot is one real, selected de-identified reference workflow when its bounded source package is available. It compares the separate experiment with the unchanged workflow-diagram skill as the control.

V1 succeeds only when one real workflow produces an accepted graph, both diagrams, and:

1. every required rubric criterion is at least `4`, with no critical failure;
2. the graph can be rendered into the Current State Workflow without re-deciding workflow meaning, and the future graph can be traced to the approved transformation map;
3. Claude execution, Codex review, one remediation, and Codex re-review run automatically, without manual hand-pasting or manual session launches; and
4. the event log provides an inspectable record of inputs, artifacts, reviews, and dispositions.

Elapsed time, iteration count, blocks/escalations, and design-owner intervention count are comparison data, not initial pass criteria. There is no automatic Git commit, push, or CI action in v1.

## Non-goals

- Do not modify or delete the existing workflow-diagram skill.
- Do not expand v1 backward into opportunity scoping, re-underwriting, or client selection.
- Do not expand v1 forward into interface design, Agent Architecture, or build implementation.
- Do not add executive workflow views.
- Do not claim that Company Brain observations automatically yield a canonical workflow graph; normalization and Gate 0 remain deliberate review work.
- Do not introduce a graph database, live Company Brain dependency, or automatic future-state transformation in v1.
- Do not adopt an ActiveGraph runtime or database before the simple event-log hypothesis is tested.
- Do not claim unattended cloud-chat orchestration before a local CLI technical spike establishes its limits.

## What We Still Need to Know

1. Criterion-level `0–5` anchors, thresholds, evidence instructions, and remediation instructions for each review gate.
2. The exact Pydantic graph schemas, source-inventory rules, graph-diff shape, event taxonomy, artifact naming, and derived state/handoff views.
3. The merge, split, gateway, contradiction, incomplete-outcome, and permitted-summary rules needed to construct one canonical graph from multiple observations.
4. The curated, versioned positive references, counterexamples, and evaluator fixtures for each packet type.
5. The local CLI technical spike: authentication, permissions, model and effort selection, session identifiers, streaming and transcript capture, failure recovery, and dashboard experience.
6. The exact de-identified reference pilot target, bounded evidence package, baseline output, and comparison method.
7. The storage layout for experiment runs, reference packs, and immutable artifacts.
8. Adoption criteria for bringing a validated experiment learning into the existing workflow-diagram skill.
9. How the later Agent Technical Design work should attach after this slice proves repeatable, without expanding this v1.

## Terminology notes

- “Current State Workflow,” “Future State Workflow,” “Company Brain,” “Agent Technical Design,” “Eval,” “Test Suite,” “Simulation,” and “Escalation” follow the active FDE working vocabulary.
- “Work packet,” “source inventory,” “coverage manifest,” “view definition,” “factory,” “canonical workflow graph,” “graph diff,” and the named gates are internal experiment labels. They are `[to confirm]` if reused in a client-facing FDE artifact.
