# Consolidated factory architecture and unified human-input model

> **Canonical copy.** This plugin holds the one canonical copy of this document (since 2026-10-01). Change it here, through the plugin's review.

_The stable design boundary for the Snapshot-to-agent-technical-design run. This document specifies
shapes, spines, gate models, and schema **deltas**. It does not edit any plugin file and does not build
any downstream stage's templates or checks — those are later work. A downstream stage designer should be
able to design any one agent-technical-design stage from this document alone, without re-deciding what the
spine, the gate model, or the schema deltas mean. It also seats the **client-facing orchestrator front
door** that opens or continues a run above this pipeline (Part 0); a downstream orchestrator-node designer
should be able to seat the front door from this document alone._

This document's internal node code is a footnote, not the handle. Where a stage, gate, or event is
named below, the **plain-language name is the primary handle**; any bracketed code (a former node id, a
gate letter, an event type) is bookkeeping.

> **Amendment note (additive, over the cleared baseline).** This is the amended keystone. It extends the
> cleared architecture spec **additively** to seat the client-facing orchestrator front door — the plugin
> must be usable by clients, not only by an FDE driving a cold end-to-end run. Every addition traces to the
> written source `the ratified orchestrator entry model`. Nothing in the cleared spec is reworked: the
> continuous FDE-driven pipeline, the five design stages, the existing cross-session resume mechanism, and
> the `fresh` (enter-at-front) end-to-end path all survive **unchanged, as special cases** of the
> generalized model. The additions are the front door as a first-class component (Part 0), a generalized
> "enter at a classified stage" control plane (Part 0), two front-door human touchpoints (§3.6), and the
> shipped-asset + guardrail model (§0.4). Where an existing statement is widened, the prior meaning is
> preserved as a named special case and the widening is line-itemed in the amendment changelog. The cleared
> baseline is retained separately as honest history.

---

## How to read this document

Five deliverables, in order (Part 0 is the amendment's addition; Parts 1–4 keep their cleared numbering so
every downstream cross-reference stays valid):

- **Part 0 — The orchestrator front door: enter at a classified stage, run to the sink.** The
  client-facing state-aware layer **above** the pipeline that opens or continues a run — the run's kickoff /
  instance boundary, the generalized control plane, and the shipped-asset + guardrail model. Architecture
  altitude only; the front door's detailed mechanics are downstream.
- **Part 1 — One continuous pipeline: Snapshot in, agent technical design out.** How the existing
  workflow-diagram half joins the new design half over one event log, and the new pull-then-verify-then-
  render front. This pipeline is what the front door enters; the cold, FDE-driven, `fresh` end-to-end run
  is the special case where entry is at the front.
- **Part 2 — The five design stages and how they are wired.** Each stage's inputs, outputs, and release
  gate(s); and the resolution of the flagship unknown (how a design loop that co-authors each stage
  against built upstream reconciles with a linear, pre-authored, release-gated control plane).
- **Part 3 — The human gates: six pipeline gates once for the whole factory, plus two front-door gates.**
  A fixed input schema per gate, mapped onto the human-decision vocabulary that already exists, plus three
  additions inside the pipeline; and (added by the amendment, §3.6) the two front-door gates that fire at
  the run's entry boundary above the pipeline.
- **Part 4 — Event-log schema deltas.** The concrete additions the new stages and gates require, as a
  delta table against the event-log schema (`reference/event-log-schema.md`); the file edits land later.

Two conventions carried throughout, from the run's honesty bar:

- **Opens are stamped, and split by kind.** A missing *data value* becomes a stamped
  `needs_more_information` field. A missing *rule or policy* becomes a named residual plus a follow-on —
  never a fabricated field and never an interim default. A missing *access* becomes a standing
  `assumptions:` entry stamped `Assumed`. This document applies the same discipline to its own opens
  (collected in the closing section).
- **No client-traceable content.** The worked reference below is a **de-identified reference agent** — no
  counterparty name, desk name, real threshold, or real surface name appears. Where the learnings source
  named a specific client artifact, it is carried as an abstracted pattern only.

---

## Part 0 — The orchestrator front door: enter at a classified stage, run to the sink

_Added by the amendment. Source of truth: `the ratified orchestrator entry model`. This part seats the front
door at **architecture altitude** — it names the component, its position, its inputs, the generalized
control plane, and the shipped-asset + guardrail model. It does **not** design the front door's internal
mechanics — entry-classification internals, the sufficiency-gate required-input manifest, and the
consented auto-heal loop are owned by the downstream orchestrator nodes and are out of scope here._

### 0.1 Why the front door exists

The plugin must be **usable by clients**, not only by an FDE driving a cold end-to-end run. The bet: AI-forward
technical stakeholders — even AI-forward business stakeholders — are trained to design agents *through the
plugin*, so a client can enter with a partial artifact or continue a run midway without a forced end-to-end
run. That requires a **state-aware layer above the pipeline** that understands the state of a run and decides
which part of the skill applies at which stage. A "fresh, enter-at-the-front" run — the cold FDE-driven
pipeline the rest of this document specifies — is the **special case** of a general "enter at a classified
stage" model. The front door does not change any pipeline stage; it sits above them (Parts 1–4 are unchanged
by it, subject to the additive state-declaration fields the amendment's coherence re-check names).

### 0.2 The front door as a first-class component (the run's kickoff / instance boundary)

- **What it is.** The **orchestrator front door** is the single entry point that **opens or continues a
  plugin run** — the run's **kickoff / instance boundary**. Every invocation of the plugin passes through it
  before any stage runs.
- **Its responsibility.** Understand the state a caller is standing in, **classify** the entry into a closed
  set (§0.3), form an **opinionated, plain-language recommendation** of where to start and why, let the
  client **confirm or override** it (the front-door human touchpoints, §3.6), and then either **resume** an
  existing run graph or **instantiate** a new one seeded at the confirmed entry stage. It *offers* an
  opinionated take; it never silently *decides*.
- **Its position.** A layer **above** the pipeline, **not a stage within it.** It is not one of the five
  design stages (Part 2) and does not ride the generic stage lifecycle; it is the boundary that decides which
  stage the pipeline enters at. It reads and writes the same one event log the pipeline uses (so a run it
  opens is indistinguishable downstream from a cold run), but it owns no design deliverable of its own.
- **Its inputs.** Existing **run state** (a valid event log + project graph at the run root) and/or a
  **client-provided artifact** (a future-state Workflow graph, or a current-state graph). One, the other, or
  neither is present; §0.3 classifies which.

### 0.3 The generalized control plane: enter at a classified stage, run to the sink

The cleared spec's control plane is "start at the front, run to the sink." The amendment **generalizes** the
*entry point* to **"enter at a classified stage, run to the sink,"** with the entry point drawn from a
**closed set** so "figure out the right start" stays bounded. Every invocation classifies into exactly one
of three entries:

- **resume** — a valid event log + project graph already exists at the run root. This is the **existing
  cross-session mechanism, unchanged**: find the project graph, read the state, pick up the next step
  (the resume read shipped as `the orchestrator's front-door helper (retired)`, over `events.jsonl` + `project-graph.md`). The amendment **seats** this mechanism
  into the entry model; it does **not** redesign it. The front door reads the same `stage.cleared` /
  `stage.accepted_with_residual` / `stage.blocked` state the resume path already reads, and recommends the
  next stage.
- **enter-with-inputs** — no run exists, but the client brings an artifact (a future-state Workflow graph,
  or a current-state graph). The front door classifies **which phase boundary the client is standing at**
  and recommends the corresponding entry stage. This is the entry that a stage's additive **entry-input
  declaration** serves (a stage declares that its consumed input may be satisfied by a client-brought
  artifact, not only by a same-run internal handoff — see the coherence re-check).
- **fresh** — nothing exists → **start at the front** (the current-state pull → verify/diff → render front,
  §1.3, then the full sequence §1.2). **`fresh` is the explicit special case**: enter-at-the-front is the
  old, cold, FDE-driven behavior, preserved exactly. Everything Parts 1–4 specify is the `fresh` path.

**Reconciling the cleared "linear control plane" language (§1.4).** The control plane is **generalized, not
contradicted.** It was linear "front → sink"; it is now linear **"entry point → sink"** for a *variable*
entry point chosen from the closed set above. From the entry stage onward it is exactly as linear,
append-only, single-writer, release-gated, and re-verifying as before — the heavy control plane of §1.4 and
§2.3 is inherited whole. The only generalization is **where the line starts**; `fresh` (start = front) is one
value of that variable. Every start therefore **either resumes a graph or instantiates one** — there is no
third path and no un-classified entry.

### 0.4 The shipped-asset + guardrail model (how the one hard rule survives orchestrator generation)

Once a run can be opened by a client rather than co-authored by an FDE, per-run human co-authoring of the
pipeline and its grading keys is replaced by **orchestrator generation from shipped assets.** The
architecture makes this safe by naming what ships and what stays load-bearing:

- **The pipeline graph ships as a versioned plugin asset.** "Create a new project graph" means
  **instantiate the shipped canonical pipeline** (the fixed stage-DAG of Parts 1–2), seeded at the confirmed
  entry stage, with run *state* carried in the event log — **not** author a bespoke DAG per run. The pipeline
  structure is a factory constant (this is the same "the human never re-authors the design-work graph"
  mechanization the cleared spec already relies on, §3.4 seam 2), now owned by the front door at
  instantiation.
- **The per-stage rubrics ship as versioned sample rubrics, and the orchestrator generates a
  task-customized rubric from them.** Human co-authoring of a stage's grading key has in practice reduced to
  *checking the rubric carries all 0–5 anchors*; with the linter enforcing anchoring, an
  orchestrator-generated rubric (customized per use case from the shipped **sample** rubrics) has worked
  well. The shipped rubrics are samples the orchestrator customizes, not per-run bespoke authoring.
- **Three shipped mechanisms are load-bearing and must not be softened** — they are what keep the **one hard
  rule** (the executor never sees its grading key) intact once human co-authoring is replaced by orchestrator
  generation:
  1. the **convention firewall (the executor/control split with opaque ids)** — the **executor stays
     isolated and never sees the rubric**, whether a human or the orchestrator produced it (§2.3; the
     firewall-hygiene check that enforces the directory split is a cross-cutting control);
  2. the **rubric linter (full 0–5 anchoring)** — every generated rubric is machine-checked for complete
     anchoring before it is used;
  3. the **Codex cross-engine reviewer** — **independence comes from Codex**, a different engine reviewing the
     Claude executor's output, never a same-family subagent (§2.3).
  A later simplification must **not** drop the executor isolation on the assumption that the linter alone
  suffices. Orchestrator-generated rubrics are one-hard-rule-compliant **only because** all three hold
  together. This is the guarantee §2.5's enforcement model already states, extended to the case where the
  rubric's *author* is the orchestrator rather than a co-authoring human: the author changes, the isolation,
  the anchoring, and the cross-engine independence do not.

### 0.5 Scope boundary of Part 0 (what is deliberately NOT designed here)

At architecture altitude the front door is a component, a classified entry set, two human touchpoints, and a
shipped-asset + guardrail model. The **internal mechanics are downstream and out of scope here**: the
entry-classification internals (how state is read and a phase boundary detected), the **sufficiency gate's
required-input manifest per stage** (the checkable completeness a stage's first consumer needs — the input
against which a client artifact's gaps are localized), the **advisory + consented auto-heal loop** (the
opinionated advisory, the client's part-by-part approval, and the enrichment that reuses the existing
pipeline stages as its healing machinery, with nothing silently re-run), and the shipped **sample-rubric
generation protocol**. A **documented first-version limitation** rides with this scope and is recorded in the
opens (closing section): the entry model reads existing state to recommend, so a **wrong state-read** is
caught at the confirm touchpoint for an FDE but a non-FDE client may not spot a bad recommendation — a
resume-robustness hardening pass is a named fast-follow, not a blocker.

---

## Part 1 — One continuous pipeline: Snapshot in, agent technical design out

### 1.1 What changes

Today the plugin's orchestrator skill is a **workflow-diagram factory**: it turns a frozen Company Brain
Snapshot into a reviewed **Current State Workflow** and **Future State Workflow**, and it stops there —
it explicitly refuses to expand into agent technical design. This run adds the design half so that one
plugin, one run, and **one event log** carry a Company Brain Snapshot all the way to a full agent
technical design and on to stage 12, which builds the agent and runs it in a Sandbox, stopping before
production launch.

The load-bearing join decision (already ratified, carried here): **the reviewed Future State Workflow
graph becomes an internal handoff, not a separate run's input.** In the prior design run the future-state
graph arrived as a cross-cutting input produced by an earlier factory run; here the two halves are one
spine over one log.

### 1.2 The end-to-end stage sequence

Plain-language names are the handle; the bracketed code is the internal stage tag used in the event log's
`stage` field.

**Existing half — the workflow-diagram factory (unchanged by this run, only mapped):**

1. **Freeze the Company Brain Snapshot** `[snapshot]` — pull the bounded process evidence and record its
   immutable identity. Unchanged.
2. **Draw the current state of the work** `[current-state]` — see the new front in 1.3. Produces the
   evidence-linked current-state graph, the seven-part mental model, the coverage manifest, and the
   rendered Current State Workflow.
3. **Agree the agent's role and authority** `[transformation]` — the transformation map, in two passes.
   First, reshape the work as named moves citing client evidence (Reshape moves, each citing Company Brain
   Snapshot or issue ids, with a no-reshape line for each move kind it does not use, and the Outcomes table).
   Then assign over the reshaped steps: each goes to an agent or a person under the write stance (the agent
   enters the values a person confirmed in its handoff), with a per-current-step trace giving each current
   step's fate; the agent's authority boundary (the Write grants table) and handbacks.
4. **Draw the future state of the work** `[future-state]` — redraw the future state from the reshaped
   steps, not copy and relabel the current graph; render the Future State Workflow.

**The seam — where the two halves meet:**

5. **Hand the reviewed future-state graph to the design half** `[internal-handoff]` — the cleared
   Future State Workflow graph is passed *within the same run* to the backbone stage. This is recorded as
   a first-class event (Part 4, `handoff.internal`) so the derived baton shows the join, and so a fresh
   session can see that the design half's input is a same-run artifact, not an external file. **(Front-door
   generalization, Part 0.)** This same-run handoff is the **`fresh`-path** provenance of the backbone
   stage's future-state-graph input. Under **enter-with-inputs**, that same input may instead be satisfied
   by a **client-brought** future-state Workflow graph standing at this boundary — the seam is the same, the
   provenance widens. The backbone stage therefore carries an additive **entry-input declaration** (named in
   the amendment's coherence re-check) so the front door can recognize this boundary; the stage's logic is
   unchanged.

**New half — the agent-technical-design factory (this run adds it):**

6. **Build the backbone — the agent's build-spec spine** `[backbone]`.
7. **Map the agent's inner team** `[internal-dag]`.
8. **Deepen each part — its inputs, decision rules, and outputs** `[deepen]`.
9. **Build the test suite and check the whole design fits together** `[eval]`.
10. **Run the design's rules on sample data before build** `[pre-build-simulation]`.

Coherence is not a stage: it is the design-coherence gate that closes stage 10 (Part 5).

**Build half — stage 12 (added 2026-10-01):**

11. **Build and run the agent in a shippable Sandbox** `[sandbox-ship]` — build the agent from the cleared design
    into a container package, reusing the pre-build simulation's data and adapters, bind each system to granted
    access or a stand-in, run the Test Suite inside the container, record Simulation evidence and the handover.
    Stops before production launch.

**Deliverable of the design half (stages 6-10):** the
AGENTS.md thin index over the build-spec spine, the agent's internal sub-agent map, the decision-policy /
rules-as-code file, the eval suite with its automated-judge instrument, and a cross-representation
coherence note — with **every sandbox, harness, model, and runtime concern, and every value the Company
Brain could not establish, stamped open** and never invented. Stage 12 consumes this package; the design half
still stamps every sandbox, harness, model and runtime concern open, and stage 12 resolves what it can and carries
the rest into its handover.

### 1.3 The pipeline front: pull, then verify and diff, then render

The empirical probe (`inputs/agent-builder-current-state-probe.md`) settles the front of every run. The
current-state step graph and process steps are **pulled from the Agent Builder tool** as a complete,
edge-connected step tree (node types, directed edges, branch conditions, tools-per-step, hierarchy,
version and source provenance). The tool emits **no diagram**. So the front becomes:

**pull structure → verify / diff → render** — replacing the prior **build-the-skeleton-from-evidence →
render**.

- **Pull** — the current-state step graph and process steps come from the Agent Builder tool largely
  as-is. This is the structure only. It arrives unverified (tool-generated draft).
- **Verify / diff — this stays skill-work, and it is where judgment lives.** The pulled structure is
  wrapped in an evidence-and-provenance layer, given an accuracy label (validated / assumed / unverified /
  no-data), and diffed against what the evidence supports. The seven-part mental model is **derived**, not
  pulled — the tool does not return it. Friction, path variants, and timing live on process observations,
  not on the step tree, so a structure-only pull returns none of them; the skill adds them. Nothing builds
  on the pulled structure until this layer has run.
- **Render — the existing Python renderer draws the diagram** from the verified graph. The renderer is the
  cheap extension; the visual-quality bar rides entirely in the renderer instructions plus the
  "check the diagram reads true" gate (Part 3), unaffected by where the structure came from.

Net effect: the step skeleton and process steps are free from the tool; executor and human effort
concentrate on the accuracy layer and the mental model. This front applies to the current-state stage of
every run. (The mechanical rewiring of the renderer to draw the tool-sourced structure is downstream
work, not designed here.)

**Stamped open (scope generalization — not access, not a data value):** the "pull largely as-is" claim is
proven for one process's structure on one workspace (source: `inputs/agent-builder-current-state-probe.md`,
Observed); breadth across arbitrary client processes is an **untested generalization** `Assumed` from the
tool's uniform schema, not yet tested on a second workspace. Carried as a **scope residual with a named
follow-on** (re-test on a second workspace), not a per-surface access open and not a fact.

### 1.4 What the design half inherits from the existing half

The design half runs on the **same heavy control plane** as the workflow half — one append-only typed
event log, one serialized re-verifying writer, release gates re-derived by that writer, the cross-engine
independent reviewer, and the derived baton / current-state / handoff views. It does **not** get a second,
lighter machinery. The reconciliation of the design half's interleaved shape with this linear control
plane is Part 2. **(Front-door generalization, Part 0.)** This heavy control plane is **entered at a
classified stage** rather than always at the front: the existing cross-session **resume** mechanism reads
its state unchanged, and **`fresh` = enter-at-front** is the special case in which the entry stage is the
current-state front. From the entry stage to the sink the plane is exactly the linear, append-only,
single-writer, re-verifying plane described here — the generalization is only where the line begins.

---

## Part 2 — The five design stages and how they are wired

### 2.0 Three graphs, do not conflate

This run touches three distinct graph objects. Keeping them separate is a hard invariant (the design half
fails if they blur):

- **The design-work graph** — the sequence of *design nodes that build the plugin* (this run's own DAG).
  Not shipped in the plugin.
- **The workflow graph** — the client's Current/Future State Workflow (the existing half's output).
- **The agent's internal map** — the agent's own sub-agent decomposition (the design half's stage 7
  output). A second graph object, distinct from the workflow graph.

Parts 2.1–2.5 define the five **stage-classes** of the agent-technical-design half. They are encoded
**generically** — any Company Brain Snapshot runs through them; the worked reference is de-identified.

### 2.1 The five stages — inputs, outputs, release gates

Each stage rides the existing generic stage lifecycle in the one log (author the packet and its withheld
grading key → execute in a fresh session → independent cross-engine review → bounded remediation loop →
clear / accept-with-open / send back). "Release gate(s)" below names the *mechanical* checks that must
have a re-verified pass, plus which human gates (Part 3) touch the stage.

---

#### Stage: Build the backbone — the agent's build-spec spine `[backbone]`

- **Inputs:** the frozen Company Brain Snapshot; the reviewed Future State Workflow graph (via the
  internal handoff); the ratified authority envelope ("Set the agent's authority boundary", Part 3);
  carried invariants.
- **Outputs:** the build-spec spine (`build-spec.yaml`, Agent-Builder-shaped: typed inputs and outputs,
  `integrations[]`, a tool-permission inventory with `must_not` carrying either `"all writes"` (a read-only agent) or `"decision-bearing writes to a system of record without a write grant"`, a `write_grants` block mirroring the agent-written rows of the transformation map's Write grants table, an end-to-end loop,
  monitoring / runbook / scope) and the AGENTS.md thin index over it.
- **Release gate(s):**
  - mechanical: the Agent-Builder structural validator (the build-spec structural check) passes; the
    honesty-stamp check passes (every committed factual claim, threshold, and decision-rule value carries
    a source and an evidence state; no invented value); the vocabulary and client-scrub checks pass.
  - human: "Approve the stage plan" (per-stage, Part 3); "Set the agent's authority boundary" (once,
    upstream, feeds this stage); "Sign off the stage".
- **Interactive session:** yes — it reads the live Agent Builder tool, which needs interactive
  authentication and so **cannot run as a spawned subagent** (see 2.3).
- **Accommodates downstream:** the build-spec declares the sub-agent surface the next stage decomposes,
  and the decision_rules the deepen stage will exceed (the Agent-Builder cap of three-to-eight
  decision_rules is a downstream concern, referenced here only so the spine leaves room for the deepen
  stage to carry the full policy elsewhere).

#### Stage: Map the agent's inner team `[internal-dag]`

- **Inputs:** the **built** build-spec (previous stage's output); activity-timeline evidence from the
  Company Brain.
- **Outputs:** the agent's internal sub-agent map (`agents.yaml`), the internal-DAG graph object, its
  rendered diagram, and a **DAG coverage manifest** (coverage of the decomposition, not a fixed sub-agent
  count).
- **Release gate(s):**
  - mechanical: the internal-map shape check passes — every write edge, write interface and agent-authoritative
    node carries an `authority_grant` (its write grant id), exactly the declared number of handoffs, and the
    review path keeps every decision no write grant backs; coverage-manifest completeness passes (a **bounded** expectation set, graded by
    coverage, not a per-line stamp — see the superseded-practice note in 2.4).
  - review-criteria (at the independent review, not a script): the orchestrator/root node is a *connected*
    node; the coordination shape is *resolved in the graph*, not carried as a provisional spec value; and
    every cross-lane interface direction is in scope — reads, outbound handoffs, the draft surface, **and
    the inbound control interface** (the direction most often missed).
  - human: "Check the diagram reads true" (this stage produces a diagram); "Approve the stage plan";
    "Sign off the stage".
- **Interactive session:** no (reads on-disk evidence and the built build-spec); subagent-allowed.

#### Stage: Deepen each part — its inputs, decision rules, and outputs `[deepen]`

- **Inputs:** the **built** internal map (`agents.yaml` and the internal-DAG graph); the live Company
  Brain.
- **Outputs:** per-sub-agent **interface designs** (typed inputs and outputs; the four evidence states
  including Contradictory; back-signals; the authority boundary; failure modes; opens stamped by kind) and
  a full **decision-policy / rules-as-code** file (`rule_sets`, `gates`, `dispatch`, `escalation_triggers`,
  `special_cases`, `open_register`, `guardrails`) that carries the policy the Agent-Builder decision_rules
  cap cannot hold.
- **Release gate(s):**
  - mechanical: the predicate-binding check passes — **every policy predicate binds to a published
    interface signal**; an unbindable predicate stops the gate, with the only two resolutions being to
    extend an interface with a *grounded* derived signal or to drop the predicate (both ratified, never
    invented — this becomes an exception, Part 3); the honesty-stamp and vocabulary/scrub checks pass.
  - template/rubric content baked in from the start (so it is not re-litigated per cycle): access is an
    `assumptions:` layer stamped `Assumed`, never a field; opens split by kind (a value open is a stamped
    `needs_more_information` field, a rule open stays a residual plus a follow-on); and an **authority
    boundary** ("the agent may not decide X; it hands to the review path") is in scope, while an
    **escalation trigger** ("if condition then escalate") is out of scope for this stage — do not author
    interim, default, or fallback behavior for anything stamped open.
  - human: "Approve the stage plan"; "Sign off the stage".
- **Interactive session:** yes — reads the live Company Brain (see 2.3).

#### Stage: Build the test suite and check the whole design fits together `[eval]`

- **Inputs:** the **built** decision-policy and interface designs (specifically the `escalation_triggers`
  and `gates`).
- **Outputs:** candidate eval cases (one case per escalation trigger, plus must-escalate negatives), a
  numeric-floor calibration (with the real floor stamped open where it needs pilot data), and the
  automated-judge instrument (judge design, judge rubrics, verdict schema, calibration set, run-judge).
- **Deliverable bar:** design-time **instrument readiness** — the judge is built and smoke-provable. An
  agent-capability result (capability-at-k, a confusion matrix) is explicitly **out of scope for the eval
  stage** and is stamped as such; stage 12 runs the Test Suite in the Sandbox, and capability-at-k needs an
  adjudicated pilot population beyond it.
- **Release gate(s):**
  - mechanical: case-coverage passes — every escalation trigger has a case and a paired must-escalate
    negative; the judge instrument runs on its calibration set. **This stage inherits the enforcement
    model (§2.5): its checks validate structure and closed sets only — case-coverage over the enumerated
    escalation triggers passed in, not a judgment of whether a case is "good"; semantic adequacy of the
    suite is the review path's job.**
  - human: "Approve the stage plan"; "Sign off the stage".
- **Interactive session:** no; subagent-allowed.

#### Gate: Check the whole design tells the same story (closes stage 10)

- **Inputs:** all built design artifacts (the build-spec spine, the internal map, the decision-policy, the
  internal-DAG diagram), the Test Suite, and the upstream pins.
- **Outputs:** a coherence matrix, a findings register, and a reconciliation note proving the build-spec,
  the internal map, the decision-policy, and the internal-DAG diagram all reconstruct the **same agent**.
- **Shape (a hard requirement, not report-only):** the gate is a **bounded drive-to-coherence loop**,
  not a checker that clears when "the reconciliation ran." Its authority to drive comes from the
  **precedence typing** it establishes: where one representation is authoritative, it drives the
  non-authoritative one into line **through the owning artifact's stage** (a co-authored, independently
  reviewed correction pass, so localization and independent review survive); where nothing is
  authoritative or a design/client decision is needed, it **escalates** a named, decision-ready residual.
- **Done-criterion:** coherence achieved (no standing semantic inconsistency) **or** all residuals
  legitimately escalated as named opens — **not** "reconciliation executed correctly." Clearing the
  coherence-matrix artifact is a valid intermediate, not the gate being done.
- **Release gate(s):**
  - mechanical: the gate's three checks, each re-verified: the coherence-matrix closure check bound to its
    submitted matrix, the design-set-current check bound to the design set the matrix pins, and the
    predicate-binding re-check bound to the pinned token index and decision policy (Part 5, "The required
    check set"). Beside them ride the no-retired-concept scan (a scannable banned set from an itemized
    "superseded — do not seed" list) and the client-scrub lint (no client-traceable strings) as a
    distributability gate. **The gate inherits the enforcement model (§2.5): its checks validate structure
    and closed token sets only (a resolvable predicate binding, a scannable banned set, a string lint) —
    they never judge whether the reconciled design is honest; that soundness judgment stays with the
    independent review and the human sign-off. Set-completeness of the design set is NOT re-owned here — it
    fired at the deepen stage; the gate reconciles representations against each other.** **The
    no-retired-concept scan and the client-scrub lint are cross-cutting controls checks (a banned-token
    scan and a client-traceable-string scan — both closed-set and structural), not coherence-specific ones;
    they too inherit the enforcement model (§2.5) and enforce structure and closed sets only, so any
    judgment of semantic honesty — whether the design is actually distributable and free of retired
    concepts in substance, not just in scanned tokens — stays with the independent review and the human
    sign-off, never the scan. The controls stage's future check-authoring brief carries this constraint,
    exactly as the other check-authoring stages' rows do.**
  - human: "Resolve a flagged blocker" for named residual escalations. The gate takes no "Sign off the
    stage" of its own: stage 10's sign-off lands with the gate current (Part 5).
- **Interactive session:** no; subagent-allowed. (The drive-to-coherence loop reuses the existing
  remediation-loop machinery — cycles, the loop controller, the escalate tier — but routes corrections to
  owning-artifact stages rather than editing in place.)

### 2.2 What is NOT a stage-class (collapses to an input)

The generic test is: a stage-class must run for *any* Company Brain Snapshot; a client-specific one-off
does not. Applying it:

- **A client-specific correction / document-sweep step** (in the learnings source this was a per-client
  doc reconciliation) is **not** a pipeline stage-class. It collapses to a **pre-condition / input step**
  feeding the current-state and backbone stages. Confirmed here.
- Any future candidate stage runs the same test before it is admitted as a stage-class.

### 2.3 The flagship reconciliation (resolving U1): a design loop on a linear control plane

The unknown: the design half co-authors each stage against **built** upstream, interleaved, with a
drive-to-coherence sink; the factory is **linear, pre-authored-brief, typed-log, release-gated**. How do
they reconcile without either giving up the factory's guarantees or forcing every stage to be pre-authored
before its inputs exist?

**Resolution: the ATD half keeps the heavy control plane and does not get a parallel "design-loop lane."
Interleaving is expressed as a two-speed brief-authoring seam on the one plane.** Concretely:

**Which stages batch after the keystone, which drop to a just-in-time seam.**

- The **keystone** of the design half is the internal handoff (the reviewed Future State Workflow graph)
  plus the ratified authority envelope. Both exist before any design stage runs.
- **Batch-after-keystone (brief instantiated up front):** the **backbone** stage. Its brief template is
  parameterized only by artifacts that exist at the keystone (the Snapshot, the future-state graph, the
  authority envelope), so the brief-drafter can instantiate and gate it before execution begins.
- **Just-in-time seam (brief instantiated only when the upstream artifact is built):** **map the inner
  team**, **deepen**, **build the test suite**, and **check the story** each consume a *built* upstream
  artifact (the built build-spec, the built internal map, the built decision-policy, the whole built set).
  Their briefs cannot be honestly pre-authored, because the brief names and SHA-pins inputs that do not
  yet exist. Each of these gets a **light just-in-time brief approval** ("Approve the stage plan") against
  the built upstream, immediately before the stage executes.

This is the dependency truth from the prior run made mechanical: the pipeline *structure* is a constant
(so graph authoring is mechanized away — see Part 3), but a stage that reads a built upstream artifact is
authored just-in-time against it.

**Which light-loop patterns are promoted into the factory:**

- the **convention firewall with the executor/control split** — the executor never sees its grading key
  (carried as an invariant; hardening in Part 4 / downstream);
- the **just-in-time interleave** for the four downstream stages (above);
- **accept-with-open** as a first-class, schema-stamped disposition ("Sign off the stage" can land a
  materially-complete-but-blocked artifact with residuals and a hydrate-at target);
- **coverage-manifest grading** (a bounded expectation set) for the map-the-inner-team stage;
- the **drive-to-coherence sink** as the shape of the coherence stage;
- the **non-convergence detector** (finding-churn on one criterion escalates the *rubric scope*, not the
  artifact);
- **open-by-kind** and **access-as-assumption** as template content;
- the **failure-family and intervention ledgers** as derived views of the one log.

**Which heavy-factory guarantees the design stages inherit:**

- the **single serialized writer** and the append-only typed log (no hand-appended events);
- **re-verified release gates** — a stage advances only on a pass the writer re-derived, never on an
  asserted one; **what those gates own vs. what they deliberately do not — structure and closed sets, never
  semantic soundness — is the enforcement model in §2.5, which every check-bearing stage builds to;**
- **cross-engine independent review** — a Claude executor is cleared only by an independent cross-engine
  reviewer, never a same-family subagent;
- the **brief criteria-manifest binding** (the score set must match the brief's own withheld criteria);
- the **time-of-clearance identity re-hash** (an artifact changed on disk after review cannot clear);
- **log/disk reconciliation** at resume.

**Interactive-auth stages in the session topology (resolving U4 at the spec level).** Two stages read a
live tool that needs interactive authentication — the backbone stage (Agent Builder tool) and the deepen
stage (live Company Brain). Interactive-auth stages **cannot run as spawned subagents**; the factory's
other executors are fresh spawned sessions. The spine therefore requires each stage to **declare its
session kind** (interactive vs spawnable) in its stage definition, and the orchestrator routes an
interactive stage as an operator-triggered fresh session (the same human-triggered-relay pattern the
existing half uses for its relays), never as a spawned subagent. (The mechanical declaration field and the
routing wiring are downstream stage-design and integration work; the spec fixes only that the distinction
is declared and honored.)

### 2.4 Superseded practices — do not seed from the learnings source

The learnings source is candidate prior-art, not gospel; several of its shapes are **retired** and must
not be reproduced in the plugin:

- **A per-client correction/doc-sweep as a stage** — superseded; it is an input step (2.2).
- **The report-only reconcile sink** — superseded by the drive-to-coherence sink (2.1, coherence stage).
- **The unbounded per-line honesty criterion** that never converged on a large spec — superseded by
  bounded coverage-manifest grading of factual claims, thresholds, and decision-rule values.
- **A rubric with collapsed score anchors** (missing intermediate rungs) — superseded by the six-anchor
  rubric standard already in the light loop's templates.
- **A prior "recommend-a-label" agent architecture and its retired predicates** (from the learnings
  source) — client-specific and superseded; not a reference pattern and never a seed.
- **All client-traceable strings** (counterparty names, desk names, real thresholds, real surface names)
  — retired from the plugin entirely; the worked reference is de-identified.

### 2.5 Where the guarantee lives: the enforcement model

A factory-produced agent technical design is a **client-distributable asset**, so the guarantee that it
is honest, complete, and coherent has to be **owned by a named layer**, not spread thinly across a
per-file check, a not-yet-built cross-check, and a human gate with gaps between them. This subsection
states that model as first-class and load-bearing: **which enforcement layer owns which part of the
guarantee**, so that every check-bearing stage — build the backbone, map the agent's inner team, deepen
each part, build the test suite and check the whole design fits together, and the design-coherence gate — is built to one model rather
than accreting its own. A **green mechanical check is never, on its own, evidence that a design is
sound**; what it *is* evidence of is stated below, layer by layer. This section is the design intent that
§2.3's inherited guarantees, §4.1's `stage.cleared`→required-check-set binding, and §4.2's check-passed
reconciliation all serve; read them together.

**Four facets, each with its owning layer.**

1. **The design set is enumerated by the orchestrator, never sniffed by a check.** The set of "these
   artifacts are the designs" is **mechanically derived from the coverage manifest** the map-the-inner-team
   stage emits (`dag-coverage-manifest.md`) — **one design per enumerated sub-agent seam** (the manifest's
   sub-agent decomposition and its seated interfaces, each carrying the `design_ref` slot the deepen stage
   fills) — and **ratified at the stage-plan gate** ("Approve the stage plan"). Each enumerated design is
   then handed to its check **explicitly, as an argument**. A check **never inspects a file to self-decide
   "am I a design"**; detection is not a check's job. This **dissolves the self-detection weakness**: there
   is no structural signature for an adversary — or a confused maintainer — to drop to slip a design past a
   check, because membership is **asserted upstream by the orchestrator, not inferred by the check**. An
   artifact is a design because the enumerated set names it, full stop.
2. **Per-file checks validate content only, inside that enumerated frame.** Once a design is named to a
   check, the check does exactly one kind of work: **required fields present, values drawn from a
   closed/enumerated set, references resolve**. Structure and closed token sets — **nothing that requires
   judging whether a claim is true**. A static check owns structure and enumerated membership; it does not
   own, and must never appear to own, whether the content is honest.
3. **Set-level completeness and coherence is a first-class gate that fires at the stage.** "Every
   enumerated design is present and well-formed" is **owned by the deepen stage** ("Deepen each part") and
   **bound to that stage's clearance** — this is the `stage.cleared`→required-check-set binding carried as a
   schema delta in §4.1, applied so a stage cannot clear until its whole enumerated design set is present
   and passes its per-file checks against the latest submitted artifact identity. This is **fired at the
   stage, not deferred to a downstream promise**. The cross-stage coherence sink — the design-coherence gate that
   closes stage 10 — remains a **separate, later gate** whose job is reconciling representations
   against each other; **set-completeness does not live there**, and no stage may lean on it to backstop a
   set the deepen stage should have completed.
4. **Semantic honesty is explicitly the review gate's job.** A passing check **never implies "honest and
   complete."** Whether a design is actually grounded, honest, and non-invented is judged by the
   **independent cross-engine reviewer and the human sign-off gate** ("Sign off the stage"), not by any
   script. Each check-bearing stage **documents this in the stage itself**, in plain words, so no
   maintainer or operator mistakes a green check for a soundness guarantee — the check attests structure and
   closed sets; the review path attests soundness.

**Two guardrails, distilled from the enforcement-architecture defect this section resolves.**

- **Checks are designed once to this model and then consolidated.** A check is authored **against the
  model above**, not grown clause-by-clause across remediation cycles. **No adversarially-accreted script**
  — one that accumulated predicates reactively as each cycle found a new evasion — **ships as a client
  asset**; a maintainer must be able to read a check and see why each clause exists.
- **"Accept with open" may never land a plugin component whose own enforcement is known to be unsound.**
  Accepting-with-open ("Sign off the stage" landing a materially-complete-but-blocked artifact with named
  residuals) is legitimate **only** for a genuine **downstream-owned residual** — a value awaiting pilot
  data, a rule a later stage owns. It is **never** a way to pass a shipped mechanism already known to be
  evadable; a known-unsound enforcement mechanism is a **blocker to fix**, not a residual to carry.

**Forward constraint (inherited by every remaining check-authoring stage).** Every stage that still
authors a check — **build the test suite and check the whole design fits together** (eval), **the design-coherence gate** (`design-coherence`),
and the cross-cutting controls stage — **inherits this enforcement model and the "checks do structure,
not semantics" rule.** A downstream brief for any of those stages carries both: the design set is
enumerated and passed in, per-file checks validate structure and closed sets only, set-level completeness
fires at the owning stage, and semantic honesty is documented as the review path's job. This constraint is
also recorded at the Build the test suite and check the whole design fits together (eval) stage row and the Check the whole design tells the same story
(design-coherence gate) row in §2.1 so it travels with their release-gate descriptions, and —
because the cross-cutting controls stage has no §2.1 row of its own — in the design-coherence gate's row, beside
which the cross-cutting client-scrub lint and no-retired-concept scan ride (the gate's own three checks are the
matrix closure, the design-set-current check and the predicate-binding re-check), so the controls stage's future
check-authoring brief carries it too.

---

## Part 3 — The human gates: six pipeline gates, plus two front-door gates

This is the unified human-input model at the **model / spec level for the whole factory** — both the
workflow-diagram half and the design half. The scope decision carried (not re-opened): **define the six
gates and the spine once for the whole factory here; the mechanical rewiring of the existing half's
scripts to this model is separate downstream work.** So below: the unified model, and how the existing
half's current gates map into it — not a rewrite of the existing half.

**The consolidation principle:** roughly twenty ad-hoc human seams observed across the prior design run
and the existing factory collapse into **six standardized gates**, each with a **fixed input schema** a
person can answer without re-reading the whole run; everything else is mechanized. This supersedes the
prior run's open-ended interview and per-node brief-authoring as human seams.

**Two tiers of human gate (amendment).** The six gates below are the **pipeline gates** — they fire
**inside** a run, per stage or once for the whole factory, and gate stage clearance. The client-facing
front door (Part 0) adds **two front-door gates** — they fire at the run's **entry boundary, above the
pipeline, per invocation**, before any stage runs, and they gate *where a run starts and what gets
enriched*, not stage clearance. So the truthful count is **six pipeline gates + two front-door gates**;
"six human gates, once for the whole factory" remains exactly true **of the pipeline tier** and is now
explicitly scoped to it. The two front-door gates get the **same fixed-input-schema treatment** as the
pipeline gates and are specified in §3.6.

### 3.1 The six gates (plain-language name is the handle)

| Plain-language name | When | What the person decides | (code) |
|---|---|---|---|
| **Confirm what we're building** | Once, at the top | Ratify the intake: agent target, scope boundary, first-version depth, known opens | G-A |
| **Approve the stage plan** | Per stage (batch or just-in-time) | Approve the drafted brief + withheld grading key for a stage; never author them | G-B |
| **Set the agent's authority boundary** | Once | Ratify the authority envelope: objective, authority boundary, retained controls, handbacks, access feasibility | G-C |
| **Check the diagram reads true** | Per diagram-producing stage | Confirm the rendered diagram is accurate and legible against the checklist, bound to the exact render | G-D |
| **Resolve a flagged blocker** | Only when a detector trips | Decide a structured exception (non-convergence, unbindable predicate, coherence residual, eval-instrument failure, uncovered-escalation trigger) | G-E |
| **Sign off the stage** | At each stage close and final acceptance | Clear / accept-with-open / send back, as a schema-stamped disposition | G-F |

### 3.2 The fixed input schema per gate

Each schema is the **minimal set of fields a person answers**, answerable without re-reading the run. A
field marked derived is pre-filled mechanically (from the Snapshot or the log) and only confirmed.

**Confirm what we're building** (G-A)
- `agent_target` — the outcome the agent is for (derived from the future-state scope where available;
  confirmed).
- `scope_boundary` — what is in and out (derived from the future-state graph where available; confirmed).
- `first_version_depth` — the definition-of-done depth for this run (operator-supplied).
- `known_opens[]` — the opens already visible from stamped gaps (derived from the Snapshot; confirmed).
- `decision` ∈ {accepted, changes-requested, rejected} + `rationale`.
- **Snapshot-derived vs operator-filled (resolving §C open 3):** derive everything derivable — scope from
  the future-state graph, opens from stamped gaps — and require the operator to supply only
  `first_version_depth` here; the authority envelope is **not** collected here (it folds into "Set the
  agent's authority boundary", G-C). This keeps the intake a confirmation, not a fresh interview.

**Approve the stage plan** (G-B)
- `stage` — the stage the plan is for (plain name).
- `brief_ref` + `brief_sha256` — the drafted executor packet under review.
- `grading_key_ref` + `grading_key_sha256` — the withheld grading key (approved, but **not** disclosed to
  the executor).
- `decision` ∈ {accepted, changes-requested} + `rationale`.
- **Batch vs interleave (resolving §C open 1):** a stage whose brief template is **Snapshot-only
  parameterized batches at the top** (right after "Confirm what we're building" / the keystone); a stage
  that **consumes a built upstream artifact gets a light just-in-time approval** immediately before it
  runs. Per Part 2.3, only the backbone stage batches; map-the-inner-team, deepen, build-the-test-suite,
  and check-the-story are just-in-time. This minimizes gates without breaking the dependency truth.

**Set the agent's authority boundary** (G-C)
- `primary_objective` — what the agent is judged on (kept distinct from the measure used to judge it).
- `authority_boundary` — what the agent may and may not decide.
- `retained_controls[]` — the controls a person keeps.
- `exception_handbacks[]` — where the agent hands to the review path.
- `access_feasibility[]` — each read surface with an access state, stamped `Assumed` until verified.
- `decision` ∈ {accepted, changes-requested, rejected} + `rationale`.

**Check the diagram reads true** (G-D)
- `stage` + `artifact_ref` + `artifact_sha256` — the exact rendered artifact (acceptance binds to this
  identity; a changed render re-triggers the gate).
- `checklist[]` — the render-accuracy + legibility items, each `{item, pass}` (accuracy: every control /
  dispatch edge terminates at its target and does not overshoot; parallel edges are offset so labels do
  not overstrike; lane/order encodes a stated semantic. legibility: labels attributable and not hidden;
  density acceptable; connectors do not cross annotation text).
- `decision` ∈ {accepted, changes-requested} + `rationale`.

**Resolve a flagged blocker** (G-E)
- `trigger` ∈ {non-convergence, unbindable-predicate, coherence-residual, eval-instrument-failure, uncovered-escalation-trigger} — which detector tripped.
- `evidence_ref[]` — the events / findings that show it.
- `resolution` — the decision-ready choice (for an unbindable predicate: extend an interface with a
  grounded signal, or drop the predicate; for non-convergence: the rubric-scope decision; for a coherence
  residual: the named open or the precedence call).
- `decision` ∈ {accepted, changes-requested} + `rationale` — **required** (the fixed
  `human.decision.decision` enum). Resolution→`decision`: `accepted` when the resolution lets the stage
  proceed on the chosen path (drop the predicate, extend an interface with a grounded signal, the
  precedence call, or the named-open escalation); `changes-requested` when the resolution sends the
  artifact back for a corrected re-run (e.g. a rubric-scope re-scope on non-convergence). `rejected` is
  unused here; no new enum value is invented. `rationale` is mandatory because this changes direction.
- `counterfactual` + `corrected_role` — required (this is a direction-changing decision; see Part 4).

**Sign off the stage** (G-F)
- `stage` — the stage (or "final acceptance").
- `disposition` ∈ {clear, accept-with-open, send-back}.
- `decision` ∈ {accepted, changes-requested} — the required `human.decision.decision` enum value the
  disposition maps to: `clear` → `accepted`, `accept-with-open` → `accepted`, `send-back` →
  `changes-requested`. `accept-with-open` has no distinct legal enum value, so it binds to the closest
  legal value `accepted` and is distinguished by `disposition` + `residuals[]` (bound-to-closest, not a
  new enum value); `rejected` is not used at this gate (a full rejection is an envelope-gate act at G-A /
  G-C). See the §4.1 disposition→terminal-event mapping.
- `residuals[]` — for accept-with-open: each residual with its kind (value / rule / access) and a
  `hydrate_at` target.
- `rationale`.

### 3.3 Mapping onto the human-decision vocabulary that already exists

The factory already carries a typed `human.decision.gate` field with **six values** — `boundary`,
`current-state-acceptance`, `future-state-remit`, `acceptance`, `feasibility`, `mid-execution-steer` — but
enforcement is partial (only the mid-execution-steer branch is enforced; there is no check that the gate
value is one of the six). The six-gate model is therefore largely a **rename + consolidation + full
enforcement** of this existing vocabulary, **plus three additions** — not a greenfield model.

**Consolidations (existing values fold into a gate):**

| Unified gate | Folds in existing value(s) | Why |
|---|---|---|
| Set the agent's authority boundary (G-C) | `boundary` + `future-state-remit` + `feasibility` | All three are the one authority / remit / access-feasibility judgment; one standardized envelope over three values. |
| Sign off the stage (G-F) | `acceptance` + `current-state-acceptance` | Both are stage-close / acceptance dispositions; one disposition schema. |
| Resolve a flagged blocker (G-E) | `mid-execution-steer` | The existing direction-changing steer becomes the structured exception, fired by a detector. |

**Additions (not in the existing vocabulary) — each typed deliberately:**

| Addition | Becomes | Why this shape |
|---|---|---|
| Confirm what we're building (G-A) | a **new `gate` enum value** `intake` | It is a ratification with the flat `{gate, decision, rationale}` shape the existing human-decision event already carries; a new enum value keeps it inside the one typed vocabulary rather than inventing a parallel event. |
| Approve the stage plan (G-B) | a **new `gate` enum value** `brief-approval` | Same flat approve / changes-requested shape; it sits on top of the existing mechanical brief gate (the check-passed set), adding a human approval that must precede the stage's `executor.started`. A new enum value, not a new event, keeps the vocabulary unified. |
| Check the diagram reads true (G-D) | a **distinct typed input** (new event kind `human.visual_review`) | It must **bind to the exact rendered-artifact SHA-256** and carry a **per-item checklist result** — richer than the flat `{gate, decision, rationale}` human-decision payload can honestly hold, and it must gate on identity the way `check.passed` does (but human-authored, because "the picture reads true" is a person's judgment a script cannot re-verify). A flat enum value would force the checklist and the SHA binding into free text; a distinct typed event makes both first-class and lets the writer bind `stage.cleared` for a diagram stage to an accepted visual review of the latest render. |

### 3.4 Reconciliation with the prior human-input design draft, adopted / superseded deliberately

The prior human-inputs design (design-only, never committed) proposed a **three-strata typed model** — (1)
baked-in standardized rules (override-only), (2) run-specific typed input, (3) overrides — with a
`design.brief` gate and a "what we still need to know" register, and it triaged sixty-four real
interventions into a set of dispositions. That design is the substrate for the disposition map here, not a
competitor. Adopt / supersede:

- **Adopt** the three strata as the mechanization frame: stratum (1) baked-in rules = the mechanized
  template content and the release checks; stratum (2) run-specific typed input = the six gates' fixed
  input schemas; stratum (3) overrides = the "Resolve a flagged blocker" exception.
- **Adopt** the `design.brief` gate shape as the concrete shape for the two "envelope" gates ("Confirm
  what we're building" and "Set the agent's authority boundary") and the "what we still need to know"
  register as the shape for the opens register (client-facing name: "What we still need to know").
**Item-by-item reconciliation of §A's disposition map against the prior human-input design draft's three
strata and its per-intervention triage.** The prior human-input design draft's triage was its own per-intervention
`MECHANIZE`-vs-standardized-gate call over sixty-four real interventions, organized under the three strata;
§A is the consolidated per-seam call for this factory. The table below reconciles each §A seam onto its
corresponding stratum in the prior human-input design draft and states, per seam, whether §A **adopts** the
prior human-input design draft's disposition or **supersedes** it — so nothing is left to a follow-on.

| §A seam (its disposition) | Stratum / element in the prior human-input design draft | Adopt / Supersede |
|---|---|---|
| 1 — Open-ended inputs interview → G-A intake (standard-gate) | Stratum 2 (run-specific typed input) + the interview-not-forms stance | **Supersede** the interview-not-forms stance with a fixed, mostly-derived intake schema (structure is now known, not being discovered); **adopt** the typed-input framing. |
| 2 — Author the design-work graph → mechanize | Stratum 1 (baked-in) | **Adopt** — the pipeline is a fixed stage-DAG, a factory constant; the human never re-authors it, matching the prior human-input design draft's baked-in stratum. |
| 3 — Author each stage's brief + grading key → mechanize (author) + standard-gate (approve), G-B | Stratum 1 (baked-in templates) + Stratum 2 (approve) + the `design.brief` gate | **Adopt** — canonical per-stage templates baked in; the human approves the instantiation (G-B) in the `design.brief` gate shape; grading key withheld. |
| 4 — Ratified rubric conventions discovered mid-run → mechanize | Stratum 1 (baked-in rules) | **Adopt** — every ratified convention becomes standard-template content up front, exactly the prior human-input design draft's stratum-1 intent. |
| 5 — Neutrality break at the cap → mechanize (detector) + exception, G-E | Stratum 3 (overrides) | **Adopt** the override→exception mapping; **supersede** the ad-hoc cap-burn override with a detector-fired, structured, decision-ready exception. |
| 6 — Accept-with-residual → standard-gate, G-F | Stratum 2 + the "what we still need to know" register | **Adopt** — a first-class schema-stamped disposition; residuals carried in the register shape the prior human-input design draft proposed. |
| 7 — Authority-boundary ratification → standard-gate, G-C | Stratum 2 + the `design.brief` gate shape | **Adopt** — one authority envelope in a fixed schema, reusing the `design.brief` gate shape. |
| 8 — Human visual gate on rendered diagrams → standard-gate, G-D | Not in the prior human-input design draft's model | **Adopt as an addition** — the prior human-input design draft typed no visual gate; G-D is a distinct typed input (`human.visual_review`). |
| 9 — Multi-artifact review bundling / cross-check wiring / wrong-brief reviewer → mechanize | Stratum 1 (baked-in rules) | **Adopt** — conventions + script hardening; no human seam. |
| 10 — Prior-art contamination catch → mechanize | Stratum 1 | **Adopt** — "superseded — do not seed" banner + a no-retired-concept release scan. |
| 11 — Predicate↔interface-signal binding catch → mechanize + exception, G-E | Stratum 1 (the check) + Stratum 3 (the override when it fires) | **Adopt** — a coherence-gate check that stops on an unbindable predicate; human only on the two ratified resolutions when it fires. |
| 12 — Client-scrub → mechanize (lint) + standard confirmation at assembly → **G-F** (final acceptance) | Not in the prior human-input design draft's model (post-dates the distributability north star) | **Adopt as an addition** — a no-client-traceable-strings lint (the coherence-stage distributability gate) + a standardized human confirmation folded into "Sign off the stage" (G-F) at final acceptance / assembly, not a separate gate. This completes the seam→gate audit map: every human seam now names its gate. |
| 13 — End-to-end coherence reconciliation → mechanize (loop) + exception (escalate residuals) | Stratum 1 (the loop) + Stratum 3 (the escalate) | **Adopt** the drive-to-coherence loop routed through owning artifacts; **supersede** any report-only reconcile stance. |

Net: the three strata are adopted as the mechanization frame; the `design.brief` gate and the "what we
still need to know" register are adopted as concrete shapes; the one stance **superseded** is
interview-not-forms (seams 1); two seams (8, 12) are net-new additions beyond the prior human-input design
draft's model.

**Provenance caveat (not an open):** the reconciliation above is **complete** against the prior human-input
design draft's model as it is summarized in §A/§D of the human-input model and the current-state grounding
memo. The sixty-four raw interventions behind the prior human-input design draft's triage buckets are not
independently re-verified from this run's inputs (the prior human-input design draft itself is not an
input). `Assumed` that the summary is faithful — this is a provenance caveat
on the summary's fidelity, **not** a deferred deliverable and **not** a per-surface access open.

### 3.5 The existing-half scope (resolving §C open 2)

Resolved as proposed: **define the six-gate model once for the whole factory at the model / spec level
(here), and leave the mechanical retrofit of the existing half's scripts as a named follow-on.** So the
existing half's current gates are **mapped** into the model now (3.3 shows workflow-diagram boundary →
G-C, the two render human-visual gates → G-D, future-state remit → G-C, the acceptance points → G-F), and
the rewiring of those scripts is downstream work — this run does not rewrite the existing half's scripts.

### 3.6 The two front-door gates (amendment) — fired at the entry boundary, above the pipeline

The client-facing front door (Part 0) adds **two human touchpoints** that fire at the run's **entry
boundary**, per invocation, before any stage runs. They are a **distinct tier** from the six pipeline
gates: a pipeline gate decides *may this stage clear*; a front-door gate decides *where this run starts*
and *what gets enriched before it proceeds*. They get the **same fixed-input-schema treatment** as the
pipeline gates — a minimal set of fields a person answers without re-reading the run, with derived fields
pre-filled — and the front door **recommends** in plain language, letting the client **confirm or
override** (it offers an opinionated take; it never silently decides). Their **internal mechanics** (how
the entry is classified, the sufficiency gate's required-input manifest, the consented auto-heal loop) are
owned by the downstream orchestrator nodes, not designed here (Part 0.5).

| Plain-language name | When | What the person decides | (front-door tier) |
|---|---|---|---|
| **Confirm where to start** | Once per invocation, at the entry boundary, before any stage runs | Confirm or override the orchestrator's opinionated, plain-language recommendation of the entry point (resume · enter-with-inputs · fresh) and the entry stage | entry-confirm |
| **Approve what to enrich** | When a client-brought artifact is thin against the entry-sufficiency bar | Approve, part by part, which of the orchestrator's advised enrichments to run before the pipeline proceeds; nothing is silently re-run | advisory-approval |

**Fixed input schema — Confirm where to start** (entry-confirm)
- `entry_classification` ∈ {resume, enter-with-inputs, fresh} — the orchestrator's classification of the
  situation (derived; confirmed).
- `recommended_entry_stage` — the plain-language stage the orchestrator recommends starting at (derived; for
  `resume`, the next step read from the existing state; for `fresh`, the current-state front).
- `recommendation_reason` — the plain-language reason for the recommendation (derived).
- `selected_entry_classification` ∈ {resume, enter-with-inputs, fresh} — the entry point the client
  actually selects, recorded **distinctly from the recommendation**. It equals `entry_classification` when
  the client accepts the recommendation, and is a different value from the same closed set when the client
  overrides. This is the classification the run actually starts on.
- `selected_entry_stage` — the plain-language stage the client actually selects to start at, recorded
  **distinctly from `recommended_entry_stage`**. It equals `recommended_entry_stage` when the client
  accepts, and is a different stage when the client overrides. This is the stage the run is actually seeded
  at.
- `decision` ∈ {accepted, changes-requested} + `rationale`. `accepted` starts the run at the recommended
  entry (resume the existing graph or instantiate a new one seeded there), so the selected fields equal the
  recommended ones; `changes-requested` is the **override** — the client names a different entry point /
  stage from the same closed set (captured in `selected_entry_classification` / `selected_entry_stage`),
  and the front door starts there instead. `rejected` is not used at this gate (declining to start is
  simply not proceeding, not a decision the schema records).

**Fixed input schema — Approve what to enrich** (advisory-approval)
- `entry_stage` — the stage the client is entering at (plain name; from the confirmed entry).
- `advised_enrichments[]` — the orchestrator's opinionated advisory: each localized gap in the client's
  artifact against the entry-sufficiency bar, with the enrichment proposed to close it, in plain language
  (derived; the sufficiency manifest that localizes the gaps is the downstream node's).
- `approved[]` — the subset of `advised_enrichments[]` the client approves to run (the client's selection).
  Unapproved parts are **not** enriched; **nothing is silently re-run.**
- `decision` ∈ {accepted, changes-requested} + `rationale`. `accepted` runs the approved enrichments
  (reusing the existing pipeline stages as the healing machinery) and proceeds; `changes-requested` sends
  the advisory back for a revised recommendation. The gate is **advisory, never strict** — it never refuses
  to interface; a thin artifact is enriched by consent, not blocked.

**Mapping onto the existing human-decision vocabulary (same discipline as §3.3).** Both front-door gates
carry the flat `{gate, decision, rationale}` shape the existing `human.decision` event already holds, plus
gate-conditional fields, so they stay **inside the one typed vocabulary** rather than inventing a parallel
event. Each becomes a **new `human.decision.gate` enum value** — `entry-confirm` and `advisory-approval` —
paralleling how §3.3 added `intake` and `brief-approval` (Part 4 carries the enum extensions and the
gate-conditional payloads). They are **not** folded into any of the six pipeline gates: an entry-boundary
decision is a different act from a stage-clearance decision, and conflating them would blur the tier
distinction this amendment is careful to keep. The **detailed payload semantics** beyond the fixed schema
above — the sufficiency-manifest structure the advisory is computed from, the auto-heal routing — are owned
by the downstream orchestrator nodes; Part 4 states only the additive event-vocabulary the front door needs.

---

## Part 4 — Event-log schema deltas

These are the concrete deltas the new stages and gates require against the event-log schema (`reference/event-log-schema.md`).
**Presented as a delta design, not an edit** — the file edits land in a later node. The new design stages
**ride the existing generic stage lifecycle** (author → execute → review → remediate → clear); they are
new **stage tags** (values of the `stage` field), **not** new event *types* per stage. So the type-level
deltas are few and deliberate.

### 4.1 Delta table

| Delta | Kind | Where | Rationale |
|---|---|---|---|
| Add the five design stages as recognized `stage` values — canonical internal tags `backbone` / `internal-dag` / `deepen` / `eval` / `coherence` (plain-language handles, primary: build the backbone / map the agent's inner team / deepen each part / build the test suite and the automated judge / check everything tells the same story) | New stage tags (no new event type) | `stage` envelope field / stage registry | The design half is one continuous pipeline on the one log; each stage reuses the generic lifecycle. The internal tag is the value written to the `stage` field, matching sections 1.2 and 2.1 exactly; the plain-language name stays the handle. Minimal surface, honors "simplify the first run". |
| Add `check.passed` to the canonical taxonomy: payload `{check, artifact_path, target_sha256, argv}`; `actor.engine` must be `script`; re-derived by the writer | New event kind (canon fix) | Event taxonomy — "Authoring / gates" section | It **exists in code** (the typed schema module and the writer's gate set) and is a real re-verified gate on `brief.authored` / `packet.authored`, but is **absent from the vendored schema doc**. This is a code-versus-canon drift; the schema must document the kind that already gates. |
| Add a "Gated authorization" subsection binding `check.passed` to the events it gates, **and extend the binding so `stage.cleared` gates on the stage's required check set** | Gating-rule addition | Event taxonomy — loop/clearance section | Today `check.passed` gates only `brief.authored` and `packet.authored`; it does **not** gate `stage.cleared`. For the design stages, `stage.cleared` must require the stage's re-verified check set (e.g. the internal-map shape check, the predicate-binding check, the coherence-and-scrub checks) bound to the latest submitted artifact SHA. State the rule so a stage cannot clear without its mechanical floor. This binding is the mechanism behind the enforcement model's third facet (§2.5): set-level completeness fires at the owning stage's clearance, and — per that model — the mechanical floor attests structure and closed sets, never semantic soundness (which is the review path's job). |
| Extend `human.decision.gate` enum with `intake` (G-A) and `brief-approval` (G-B) | Enum extension | `human.decision` payload | The two additions that fit the flat `{gate, decision, rationale}` shape; keeps them in the one typed vocabulary. Also: **add an enum check** on the gate value (today none exists) so all eight values are enforced, not just the steer branch. |
| Extend the `human.decision` payload for the intake gate (G-A) with `{agent_target, scope_boundary, first_version_depth, known_opens:[…]}` (gate-conditional; present only when `gate == intake`) | Payload extension | `human.decision` payload | "Confirm what we're building" (G-A, §3.2) ratifies the intake in a fixed schema: `agent_target` / `scope_boundary` / `known_opens[]` are Snapshot-derived and confirmed, `first_version_depth` is operator-supplied. Typed so the intake is a confirmation, not free text. Consistent with the vendored payload's optional-field pattern (`what_changed?`, `implies_brief_defect?`). |
| Extend the `human.decision` payload for the brief-approval gate (G-B) with `{brief_ref, brief_sha256, grading_key_ref, grading_key_sha256}` (gate-conditional; present only when `gate == brief-approval`) | Payload extension (approval identity) | `human.decision` payload | "Approve the stage plan" (G-B, §3.2) must bind to the exact drafted executor packet and its withheld grading key by SHA-256 — the key approved but never disclosed to the executor (the convention firewall). The flat `{gate, decision, rationale}` payload cannot carry the two identity bindings. |
| Extend the `human.decision` payload for the authority-boundary gate (G-C) with `{primary_objective, authority_boundary, retained_controls:[…], exception_handbacks:[…], access_feasibility:[{surface, access_state, evidence}]}` (gate-conditional; present when `gate == boundary`, the consolidated value) | Payload extension (authority inputs) | `human.decision` payload | "Set the agent's authority boundary" (G-C, §3.2) consolidates the existing `boundary` + `future-state-remit` + `feasibility` values into one envelope (§3.3); the typed authority inputs must be first-class, and `access_feasibility[]` carries each read surface stamped `Assumed` in the assumptions layer until verified (never a fabricated field, never an interim default). |
| Extend the `human.decision` payload for the exception gate (G-E) with `{trigger ∈ {non-convergence, unbindable-predicate, coherence-residual, eval-instrument-failure, uncovered-escalation-trigger}, evidence_ref:[…], resolution, decision ∈ {accepted, changes-requested}, rationale, counterfactual, corrected_role}` (gate-conditional; present when `gate == mid-execution-steer`). Resolution→`decision`: a resolution that lets the stage proceed on the chosen path (drop the predicate, extend an interface with a grounded signal, the precedence call, or the named-open escalation) → `decision: accepted`; a resolution that sends the artifact back for a corrected re-run (e.g. a rubric-scope re-scope on non-convergence) → `decision: changes-requested`. No new enum value; `rejected` is unused at G-E | Payload extension (exception resolution) | `human.decision` payload | "Resolve a flagged blocker" (G-E, §3.2) is a direction-changing decision fired by a detector; it must record which detector tripped, the evidence, the decision-ready resolution, the **required `decision` enum value** (mapped above) and its `rationale`, and — because it changes direction — a `counterfactual` and `corrected_role` (paired with the run's counterfactuals corpus). The flat payload cannot hold the structured exception; the `decision` field satisfies the vendored `human.decision` required-field set using only the fixed enum. |
| State the **double mapping** from the "Sign off the stage" (G-F) disposition to (i) the required `human.decision.decision` enum value and (ii) the terminal event: `clear → decision: accepted → stage.cleared`; `accept-with-open → decision: accepted → stage.accepted_with_residual`; `send-back → decision: changes-requested → stage.blocked`. `accept-with-open` has no distinct legal `decision` value, so it **binds to the closest legal value `accepted`** and is distinguished by `disposition: accept-with-open` + `residuals[]` (bound-to-closest + noted, not a new enum value); `rejected` is unused at G-F (a full rejection is an envelope-gate act at G-A / G-C). The G-F `human.decision` (existing `acceptance` / `current-state-acceptance` gate values, plus `decision`, `disposition`, `residuals[]`) authorizes the terminal event and the writer emits it | Gating-rule / disposition mapping | Loop / clearance section + `human.decision` payload | Maps each disposition to a legal value of the fixed `human.decision.decision` enum `{accepted, rejected, changes-requested}` AND to the terminal event it selects, so an accept-with-open lands `stage.accepted_with_residual` (residuals by kind + `hydrate_at`) rather than being buried in a note, and a send-back lands `stage.blocked`. Ties the six-gate dispositions to **both** the existing decision enum and the terminal-event vocabulary; no new `decision` enum value is invented. |
| Add `human.visual_review` event kind: payload `{stage, artifact_path, artifact_sha256, checklist:[{item, pass}], decision ∈ {accepted, changes-requested}, rationale}` | New event kind (typed input) | Human-gate section | "Check the diagram reads true" (G-D) needs SHA binding + a per-item checklist that the flat human-decision payload cannot hold; and `stage.cleared` for a diagram-producing stage must bind to an `accepted` visual review of the latest render (the human accuracy/legibility judgment a script cannot re-verify). |
| Add `handoff.internal` event kind: payload `{from_stage, to_stage, artifact_path, sha256}` | New event kind | Run-structure section | Makes the join first-class: the reviewed Future State Workflow graph passing to the backbone stage within the same run, so the derived baton shows the seam and a fresh session sees the design-half input is a same-run artifact, not an external input. |
| Add `diff.verified` event kind: payload `{source, source_sha256, verified_against, accuracy_label ∈ {validated, assumed, unverified, no-data}, coverage}` | New event kind | Stage-0 / front section | Records the verify/diff layer over the Agent-Builder pull (pull → verify/diff → render). The pulled structure is unverified draft; this event carries the accuracy label the skill attaches before anything builds on it. |
| Extend `snapshot.frozen.source` enum with `agent-builder` (the pulled current-state structure) and the activity-timeline pull | Enum extension | `snapshot.frozen` payload | The front now pulls current-state structure from the Agent Builder tool, and the map-the-inner-team stage consumes activity-timeline evidence; both are frozen sources beyond the existing `company-brain` / `fde-vocab`. |
| Add `stage.accepted_with_residual` as a terminal event kind: payload `{cycle, gate_ref, residuals:[{id, kind ∈ {value, rule, access}, hydrate_at}]}`; human-gated (G-F) | New event kind (terminal) | Loop / clearance section | The prior run proved a materially-complete-but-rubric-blocked artifact needs a clean, honest landing (accept-with-open). Make it a first-class disposition alongside `stage.cleared` / `stage.blocked`, schema-stamped with residuals by kind and a hydrate-at target, so opens are carried honestly rather than buried in a note. |
| Extend `escalated.reason` enum with `moving-target-rubric` | Enum extension | `escalated` payload | The non-convergence detector: a criterion below bar with non-overlapping findings across cycles escalates the **rubric scope** (→ "Resolve a flagged blocker"), rather than burning remediation cycles on the artifact. |
| Add optional `session_kind ∈ {interactive, spawnable}` to the stage registry / `packet.authored` | Field addition | Stage definition / `packet.authored` | The backbone and deepen stages read a live tool needing interactive auth and cannot be spawned subagents; the log must let a stage declare its session kind so the orchestrator routes it correctly (U4). |
| **(Front door, Part 0 / §3.6)** Extend `human.decision.gate` enum with `entry-confirm` and `advisory-approval` | Enum extension | `human.decision` payload | The two front-door touchpoints (§3.6) fire at the entry boundary above the pipeline; both carry the flat `{gate, decision, rationale}` shape, so a new enum value keeps them inside the one typed vocabulary (paralleling `intake` / `brief-approval`). The enum check added above enforces all values. |
| **(Front door)** Extend the `human.decision` payload for `entry-confirm` with `{entry_classification ∈ {resume, enter-with-inputs, fresh}, recommended_entry_stage, recommendation_reason, selected_entry_classification ∈ {resume, enter-with-inputs, fresh}, selected_entry_stage}` (gate-conditional; present only when `gate == entry-confirm`) | Payload extension | `human.decision` payload | "Confirm where to start" (§3.6) records the orchestrator's classified entry, the recommended entry stage, and the plain-language reason the client confirms or overrides, **plus the client-selected entry classification and stage recorded distinctly from the recommendation** so an override captures where the run actually starts rather than losing it to free text. `decision ∈ {accepted, changes-requested}` uses the fixed enum (override = `changes-requested`, with `selected_entry_classification` / `selected_entry_stage` naming the different entry from the closed set; on `accepted` the selected fields equal the recommended ones; `rejected` unused). |
| **(Front door)** Extend the `human.decision` payload for `advisory-approval` with `{entry_stage, advised_enrichments:[…], approved:[…]}` (gate-conditional; present only when `gate == advisory-approval`) | Payload extension | `human.decision` payload | "Approve what to enrich" (§3.6) records the entry stage, the opinionated advisory (localized gaps + proposed enrichments), and the client-approved subset; unapproved parts are not enriched and **nothing is silently re-run**. The advisory is never strict. `decision ∈ {accepted, changes-requested}`. The **structure of the sufficiency manifest** the advisory is computed from and the **auto-heal routing** are owned by the downstream orchestrator nodes, **not** specified here. |
| **(Front door)** Add an optional **entry-input declaration** to the stage registry — a stage may declare that its consumed input is satisfiable by a **client-brought artifact** (enter-with-inputs), not only by a same-run internal handoff, plus the sufficiency signature the front door's gate scores against | Field addition (state-declaration) | Stage definition | The generalized control plane (Part 0.3) lets a run **enter at a classified stage**. For the front door to recognize an entry boundary and localize a client artifact's gaps, an entry-point stage must **declare** what input satisfies it (the backbone stage for the ATD half; the current-state / future-state stages for the existing half). This is **additive and does not change stage logic** (the amendment's coherence re-check names which stages need it). The concrete per-stage **required-input manifest** — the checkable completeness — is owned by the downstream sufficiency-gate node; the stage-definition slot lands at integration. |

### 4.2 The check-passed reconciliation, stated plainly

- **What the schema must add:** the `check.passed` event kind (payload, `actor.engine == script`,
  writer-re-derived), documented in the taxonomy; and a "Gated authorization" subsection that lists, per
  authorizing event, the check set that must have a re-verified pass bound to the same artifact SHA.
- **What gating rule must bind it:** today the writer binds the check set to `brief.authored` and
  `packet.authored` only. The delta is to **also bind `stage.cleared` (and the new
  `stage.accepted_with_residual`) to each stage's required checks** — so a design stage cannot clear on a
  passing review alone; its mechanical floor (shape check, predicate-binding, coverage, scrub, and — for a
  diagram stage — an accepted visual review) must also be re-verified against the latest submitted SHA.
  Passing the independent review is necessary but not sufficient; the mechanical gate binds too.
- **What this binding does and does not attest (the enforcement model, §2.5):** the re-verified mechanical
  floor attests **structure and closed/enumerated sets** — required fields, values in a closed set,
  references resolve, the enumerated design set present and well-formed — over a design set the orchestrator
  **enumerated and passed in** (a check never self-detects "am I a design"). It does **not** attest that a
  design is honest, grounded, and non-invented; **that soundness judgment is owned by the independent
  reviewer and the human sign-off gate.** So the rule is symmetric: a stage cannot clear on review alone,
  and a green check is never read as a soundness guarantee.

### 4.3 What is deliberately NOT changed

- No new event *type* per design stage — the generic lifecycle is reused (one log, one pipeline).
- No change to the single-writer, flock-serialized, re-verify-and-rollback mechanics — the design stages
  inherit them unchanged.
- No change to the cross-engine independence rule or the criteria-manifest binding.
- The actual schema-file edits, the check scripts, and the stage templates are downstream — this is the
  delta design only.

---

## Sources and evidence states for committed claims and values (honesty bar)

The bounded set of committed current-state claims (§1.1, §1.3, §1.4, §3.3) and committed design values
(Parts 2–4) each carry a source and one of the four evidence states (Observed / Assumed / Contradictory /
needs more information). Nothing below is invented; a value asserted without a grounding source is stamped
`Assumed`, and anything unestablished is an open (next section), not a committed value.

| Committed claim / value (where) | Source | Evidence state |
|---|---|---|
| The plugin's orchestrator is today a workflow-diagram factory that terminates at a reviewed Future State Workflow and refuses to expand into agent technical design (§1.1) | `within/SKILL.md` | Observed |
| The reviewed Future State Workflow graph becomes an internal handoff within one run, not a separate run's input (§1.1) | `inputs/inputs-capture.md`, `design-log.md` (ratified join) | Observed |
| The Agent Builder pull returns a complete, edge-connected step tree (node types, edges, branch conditions, tools-per-step, hierarchy, version/source) and emits no diagram (§1.3) | `inputs/agent-builder-current-state-probe.md` | Observed (one process, one workspace) |
| The accuracy-label set is `{validated, assumed, unverified, no-data}` (§1.3, §4.1 `diff.verified`) — the probe's source token for the last value is `no_data`; it is **normalized here to kebab-case `no-data`** to match the vendored schema's enum convention (`company-brain`, `current-state-acceptance`, `accept-with-open`), a deliberate design normalization, not a silent misquote | `inputs/agent-builder-current-state-probe.md` | Observed |
| The design half runs on the same heavy control plane — one append-only typed log, a single serialized re-verifying writer, writer-re-derived release gates, the cross-engine independent reviewer, derived baton/current-state/handoff views (§1.4) | `the vendored event-log schema (`reference/event-log-schema.md`)`; `within/scripts/` (`append-event.sh`, `loop-controller.sh`, `invoke-reviewer.sh`, `_schema.py`) | Observed |
| The existing `human.decision.gate` enum has six values (`boundary`, `current-state-acceptance`, `future-state-remit`, `acceptance`, `feasibility`, `mid-execution-steer`) (§3.3) | `event-log-schema.md` (human-gate section); `the current-state grounding evidence` | Observed |
| Enforcement is partial — only the `mid-execution-steer` branch is enforced; no enum check on the gate value (§3.3) | `append-event.sh` (`enforce_payload`); `_schema.py` (requires only `["gate","decision"]`); `the current-state grounding evidence` | Observed |
| `check.passed` is a real, script-authored, writer-re-derived gate on `brief.authored` / `packet.authored`, but is absent from the event-log schema (`reference/event-log-schema.md`) and does not gate `stage.cleared` (Part 4) | `the current-state grounding evidence` | Observed |
| The two-cycle remediation-loop default (`max_cycles: 2`, then escalate) (Part 2) | `event-log-schema.md` (loop model / `loop_policy`) | Observed |
| The Agent-Builder `decision_rules` cap of three-to-eight, which the deepen stage's rules-as-code exceeds (Part 2) | `inputs/inputs-capture.md` (tool constraint) | Observed |
| The tool-permission inventory commits `must_not = [all writes]` for the reference agent (Part 2) | `design-log.md`, `inputs/inputs-capture.md` (ratified design decision) | Observed (design commitment) |
| Roughly twenty ad-hoc human seams consolidate into six standardized gates (Part 3) | `human-input-model.md` §A/§B | Observed (design synthesis over the enumerated seams) |

The remaining **committed design-value classes** in Parts 2–4 (grouped by class, per the convention — one row per bounded class, not per value):

| Committed design-value class (where) | Source | Evidence state |
|---|---|---|
| The five design stage-classes (backbone / map-the-inner-team / deepen / build-the-test-suite / check-the-story) and each stage's inputs, outputs, and release-gate set (§2.1) | `inputs/inputs-capture.md` (ratified five stage-classes + first-version bar); learnings source (the prior ATD zero-to-one run: `the failure-modes ledger`, `counterfactuals-log.md`, `run-closeout.md`) | Observed (ratified framing + design synthesis) |
| The stage-class admission test, and the client-specific correction / doc-sweep collapsing to an input rather than a stage (§2.2, §2.4) | `inputs/inputs-capture.md`; learnings source | Observed (design synthesis) |
| The U1 resolution — batch-after-keystone (backbone only) vs just-in-time brief approval for the four stages that consume a built upstream artifact (§2.3) | `inputs/inputs-capture.md` (U1); `design-log.md` (dependency truth) | Observed (design synthesis over the dependency truth) |
| The light-loop patterns promoted into the factory and the heavy-factory guarantees the design stages inherit (§2.3) | learnings source (the prior ATD zero-to-one run); `within/scripts/`; `the current-state grounding evidence` | Observed (design synthesis) |
| The interactive-auth session-kind routing resolving U4 — backbone + deepen cannot be spawned subagents; `session_kind` declared and honored (§2.3, §4.1) | `inputs/agent-builder-current-state-probe.md` (interactive auth need); `inputs/inputs-capture.md` (U4) | Observed (probe) for the auth need; Assumed for the routing mechanics (downstream wiring) |
| The per-stage release rules stated by role — backbone structural + honesty-stamp + scrub; internal-map shape check; deepen predicate-binding; eval case-coverage; the design-coherence gate's matrix closure + design-set-current + predicate-binding re-check, with the cross-cutting no-retired-concept scan and client-scrub lint beside it (§2.1) | design synthesis over `inputs/inputs-capture.md` + learnings source; concrete check names/thresholds are a named rule/policy residual (see Split-by-kind below) | Assumed (specified by role; the concrete check set is a residual, not a committed value) |
| The six human gates and their fixed input schemas, and the consolidation principle (§3.1–3.2) | `human-input-model.md` §B | Observed (design synthesis) |
| The gate → existing `human.decision.gate` enum mapping, the three consolidations, and the three additions with their typing (§3.3) | `event-log-schema.md` (human-gate section); `human-input-model.md` §D | Observed |
| The seam→gate audit map, including seam 12 (client-scrub confirmation) → G-F (§3.4) | `human-input-model.md` §A; this spec's consolidation | Observed (design synthesis) |
| The prior human-input design draft's adopt/supersede reconciliation — the three strata, the `design.brief` gate shape, the "what we still need to know" register, the 13-seam map (§3.4) | `human-input-model.md` §A/§D; `the current-state grounding evidence` (prior human-input design draft summary) | Assumed (faithful to the prior human-input design draft summary; the raw 64 interventions not re-verified — see the §3.4 provenance caveat) |
| The event-log schema deltas — new stage tags; new event kinds (`handoff.internal`, `diff.verified`, `human.visual_review`, `stage.accepted_with_residual`); enum extensions (`intake`, `brief-approval`, `agent-builder`, `moving-target-rubric`); gate-conditional payload extensions (§4.1) | `the vendored event-log schema (`reference/event-log-schema.md`)` (vendored); `the current-state grounding evidence` | Observed (delta design grounded in the vendored schema + the code) |
| The JIT / batch routing expressed as the G-B batch-vs-interleave decision, and the disposition → `human.decision.decision` enum value mapping for G-E / G-F using only the fixed enum `{accepted, rejected, changes-requested}` (§3.2, §4.1) | `event-log-schema.md` (`human.decision` payload; terminal events); `inputs/inputs-capture.md` (U1) | Observed |
| The `check.passed` code↔canon reconciliation and the new rule binding `stage.cleared` / `stage.accepted_with_residual` to each stage's re-verified check set (§4.2) | `the current-state grounding evidence`; `within/scripts/` (`append-event.sh`, `_schema.py`) | Observed |
| The enforcement model — the four owning layers, the two guardrails, and the forward constraint (§2.5) — including the current-state sub-claims that `check.passed` re-verifies today and gates `brief.authored` / `packet.authored` but not yet `stage.cleared`, and that the design set is enumerated one-design-per-sub-agent-seam from the coverage manifest | `the failure-modes ledger` (FM-2, the blocking enforcement-architecture defect this model resolves); `the shipped internal-DAG coverage-manifest template (`templates/dag-coverage-manifest.template.md`)` (the enumerated sub-agent + interface set the design set derives from); `the current-state grounding evidence` (`check.passed` gating state); the §4.1 `stage.cleared`→required-check-set binding | Observed (current-state sub-claims) + Observed (design commitment: the model synthesized over FM-2 and the §4.1 binding) |



**Amendment committed claims (Part 0, §3.6, and the front-door §4.1 rows) — source + evidence state.** Every
addition traces to the written source resolved in the 2026-09-10 session; nothing is invented.

| Committed claim / value (where) | Source | Evidence state |
|---|---|---|
| The plugin must be client-usable, forcing a state-aware front-door **layer above** the pipeline that is the run's kickoff / instance boundary; `fresh` (enter-at-front) is the special case (Part 0.1–0.2) | `the ratified orchestrator entry model` (why-this-exists; front-door-above-the-pipeline) | Observed (ratified 09-10) |
| The closed entry set `{resume, enter-with-inputs, fresh}`, and that every start either resumes a graph or instantiates one (Part 0.3) | `the ratified orchestrator entry model` §1 (entry classification, closed set) | Observed (ratified 09-10) |
| `resume` is the **existing** cross-session mechanism, unchanged (find the project graph, read state, pick up the next step over `events.jsonl` + `project-graph.md`) (Part 0.3) | `the ratified orchestrator entry model` §1; `project-graph.md` (the run's own resume mechanism) | Observed |
| The pipeline **graph** and the per-stage **rubrics** ship as versioned plugin assets; instantiate-the-shipped-pipeline (not author a bespoke DAG); the orchestrator generates a task-customized rubric from shipped **sample** rubrics (Part 0.4) | `the ratified orchestrator entry model` §2 | Observed (design commitment, ratified 09-10) |
| The firewall (executor/control split), the rubric linter (full 0–5 anchoring), and the Codex cross-engine reviewer are load-bearing shipped design that keep the one hard rule intact under orchestrator-generated rubrics; executor isolation must not be dropped (Part 0.4) | `the ratified orchestrator entry model` §2 (guardrail, non-negotiable); §2.3 / §2.5 (the inherited guarantees) | Observed (design commitment) |
| The two front-door gates ("Confirm where to start" / entry-confirm; "Approve what to enrich" / advisory-approval), their fixed input schemas, the advisory-not-strict + nothing-silently-re-run behavior, and their enum/payload treatment (§3.6, §4.1) | `the ratified orchestrator entry model` §1, §3, §4; this spec's §3.3 typing discipline | Observed (design synthesis over the ratified entry model) |
| The additive **entry-input declaration** on entry-point stage definitions (backbone; existing-half current-state / future-state), logic unchanged, with the required-input manifest owned downstream (§4.1) | `the ratified orchestrator entry model` §3 + "what this changes downstream"; the amendment coherence re-check | Observed (the additive need); Assumed (the sufficiency-gate-vs-integration ownership split — downstream wiring) |
| That the front door's internal mechanics — entry-classification internals, the sufficiency-gate required-input manifest, the consented auto-heal loop, the sample-rubric generation protocol — are downstream and out of scope here (Part 0.5) | `the ratified orchestrator entry model` §3–§4; `project-graph.md` (downstream front-door node scope) | Observed (scope decision) |

Split by kind, per the run's convention:

- **Documented first-version limitation (front door, per the source's "known limit — document, do not
  solve now"):** the entry model reads existing state to recommend, so a **wrong state-read** is caught at
  "Confirm where to start" for an FDE but a **non-FDE client may not spot a bad recommendation** → a silent
  wrong start. Recorded as a first-version limitation with a named fast-follow (a resume-robustness
  hardening pass); **not** a blocker and **not** a fabricated value. `Assumed` (the risk is real; the
  hardening is deferred).
- **Data / value open** (`needs_more_information`): none at the document level — this spec commits no
  fabricated value. The agent-design value opens it points to (e.g. the eval numeric-floor calibration that
  needs pilot data, Part 2) are stamped `needs_more_information` at their own stage in Part 2, not carried
  as this document's opens.
- **Rule / policy open** (residual + named follow-on, never an interim default): (a) the exact required
  **release-check set per design stage** — the named checks each stage's `stage.cleared` must bind to — is
  a release-check *requirement*, not a data value; it is specified by role above, but the concrete check
  names and thresholds are produced by the downstream stage-design nodes. Residual with a named follow-on
  to those nodes; no interim default. (b) the precise loop-policy for the drive-to-coherence stage (max
  cycles before a residual is escalated rather than driven) is a residual with a named follow-on to the
  coherence-stage design; no interim default.
- **Access open** (`Assumed`, per-surface, in an `assumptions:` layer): none at the document level — the two
  interactive-auth surfaces this spec relies on (the Agent Builder tool and the live Company Brain) are
  established as accessible by the probe. Per-surface access for a specific agent design is carried in the
  G-C `access_feasibility[]` layer stamped `Assumed`, not here.
- **Scope-generalization assumption** (a scope residual + named follow-on; distinct from per-surface
  access): the "pull largely as-is" breadth across arbitrary client processes is an untested generalization
  — proven for one process's structure on one workspace, `Assumed` from the tool's uniform schema, not yet
  tested on a second workspace. Carried as a scope residual with a named follow-on (re-test on a second
  workspace); **not** an access open.
- **Provenance caveat** (distinct from an open): the item-by-item reconciliation in 3.4 is complete against
  the prior human-input design draft's model as summarized in §A/§D and the grounding memo; the sixty-four
  raw interventions behind the prior human-input design draft's triage buckets are not independently
  re-verified from this run's inputs (the prior human-input design draft itself is not an input). `Assumed`
  that the summary is faithful — a source-fidelity caveat, not a deferred
  deliverable and not a per-surface access open.

_Internal-code footnote for cross-reference only (not the handle): stages
backbone / internal-dag / deepen / eval / coherence; pipeline gates G-A…G-F; front-door gates
entry-confirm / advisory-approval (§3.6); the front-door layer is added by this amendment; its detailed mechanics are owned by the downstream front-door nodes._

---

# Part 5 - Stage methods (the ATD build half)

_The detailed per-stage method for each design-half stage. The architecture above (Parts 0-4) is
the stable design boundary; the sections below are the buildable method each stage's executor packet
and review brief are drafted from. Each stage's templates, checks, and control-side rubric are named
in `SKILL.md` and shipped under `templates/`, `checks/`, `eval-stage/`, and (as test support) `tests/knowledge/control-templates/`._

> **Reading note (integration).** These stage-method sections are folded from the design-half stage
> records. Where they cite the *originating run's* evidence as provenance — `inputs/*` capture/probe files,
> `design-log.md`, the failure-modes ledger, `counterfactuals-log.md`, `run-closeout.md`, the six-gate
> human-input-model design record `human-input-model.md`, or a committed
> `DRYRUN-RESULTS.md` transcript — those name that run's honest history and are **not shipped artifacts**;
> the shipped checks reproduce their own dry-run transcripts when run. Where the eval-stage method names
> a fuller design-time eval architecture (a checks-engine, a trace-wiring module, a harness runner, a
> results reader, and a done-set deriver), those describe the design-time
> eval architecture; the **first version ships the minimal, self-contained eval module**
> `eval-stage/framework/evals/{checks/engine,judge,eval_set_consumer}.py` — enough to run the shipped eval
> checks + `run-judge.py` end to end — and the fuller modules are **named, not shipped**. Runtime artifacts
> a stage produces (`*.graph.json`, `build-spec.yaml`, `interface-design.yaml`, the coverage manifests,
> `agents.yaml`, `AGENTS.md`) are run outputs, not shipped files.


# Stage — Build the backbone: the agent's build-spec spine

_A staged SKILL section for the agent-technical-design half of the factory. It mirrors the existing
`within` SKILL stage style; it does not edit that file. Integration and wiring are a
later node. The plain-language stage name is the handle; the bracketed internal tag is a footnote._

Internal stage tag (a footnote, not the handle): `backbone`.

## What this stage is

The first stage of the design half. It turns the reviewed **Future State Workflow** graph, the frozen
**Company Brain Snapshot**, and the ratified authority envelope into the **build-spec spine** the whole
agent technical design hangs on — an Agent-Builder-shaped `build-spec.yaml` and the AGENTS.md thin index
over it. It is the spine, not the depth: it fixes the agent's outer boundary and leaves the inner team and
the decision policy to the stages that follow.

The spine is authored to be **generic and distributable** — no client-, counterparty-, desk-, or real
surface name appears in it. A worked reference is a de-identified reference agent, never a named client's
agent.

## Purpose

Produce the stable outer shape of the agent so that:

- a downstream stage designer can locate the sub-agent **surface** to decompose and the decision-policy
  **room** to fill, without re-deriving what the backbone produces or where it sits on the pipeline;
- every committed value carries a source and an evidence state, and every gap is a stamped open — nothing
  invented;
- the agent's authority follows the write stance: it makes the writes the transformation map's Write grants
  table grants it (notices, drafts, handoffs, its own files, mechanical writes of person-confirmed values).
  By default a decision-bearing system-of-record write is person-routed: a person decides and writes from the
  agent's handoff. The only exceptions are the two named Exception reasons in the Write grants table,
  `own-working-surface` and `reversible-reviewed`, each stamped Assumed and listed in What we still need to
  know; the authority boundary follows that grant's register row. The tool-permission inventory commits `must_not` carrying either `"all writes"` (a read-only agent) or `"decision-bearing writes to a system of record without a write grant"`; read-only is a valid choice.

## Inputs

- the frozen **Company Brain Snapshot** (immutable frozen-input identity, pinned by SHA-256);
- the reviewed **Future State Workflow** graph, arriving via the **internal handoff** (a same-run
  artifact, not an external file — recorded as `handoff.internal` so the derived baton shows the seam);
- the ratified **authority envelope** — objective, authority boundary, retained controls, exception
  handbacks, and per-surface access feasibility — from the "Set the agent's authority boundary" gate;
- the invariants carried forward (or the note that this is the first version).

## Outputs

- **`build-spec.yaml`** — the build-spec spine, from `templates/build-spec.template.yaml`: typed `inputs`
  and `outputs`; `integrations[]` (`access_mode` `read` / `write` / `read-write`, each write integration
  naming its `write_grants`); a top-level `write_grants` block (the agent-written register rows); a
  `tool_permission_inventory` with `must_not` carrying either `"all writes"` (a read-only agent) or `"decision-bearing writes to a system of record without a write grant"`; the
  declared **sub-agent surface** (a placeholder, not a decomposition); **decision-rule room** left for the
  deepen stage (the Agent-Builder `decision_rules` cap of three-to-eight is referenced, never filled); an
  `end_to_end_loop`; and `monitoring` / `runbook` / `scope`.
- **the AGENTS.md thin index** over the spine, from `templates/agents-thin-index.template.md` — the thin
  index only, never the sub-agent DAG (that is the internal-map stage).

## What this stage declares for downstream (its lane)

- It **declares the sub-agent surface** the next stage — "Derive the agent's internal map" — will
  decompose. A single named surface, no sub-agent list, no handoff count, no internal edges.
- It **leaves room** for "Deepen each sub-agent" to carry the full decision policy in a separate file. The
  `decision_rules` cap is cited so the spine does not try to hold a policy the tool cannot; the backbone
  authors no rule.
- It authors no interface designs and no decision-policy file. Referencing that they exist downstream is
  required; producing them is out of this stage's lane.

## Release gates

**Mechanical (each must be a re-verified `check.passed`, bound to the latest submitted artifact SHA-256):**

- the **Agent-Builder structural validator** — `checks/check-build-spec-structure.sh` — the backbone
  stage's own mechanical floor: required blocks present and typed; `must_not` carries exactly one of
  `"all writes"` or `"decision-bearing writes to a system of record without a write grant"`; every write
  integration names grant ids present in `write_grants`, each stamped `Assumed`; the
  sub-agent surface declared but not decomposed; no invented decision-rule values past the cap; every
  honesty stamp well-formed;
- the **honesty-stamp check** — every committed factual claim, threshold, and decision-rule value carries
  a source and an evidence state; no invented value (the structural validator enforces the well-formed
  stamp; the honesty-stamp check enforces coverage — its concrete rule is a downstream cross-cutting
  control, referenced here, not authored in this node);
- the **vocabulary and client-scrub checks** — FDE-vocabulary-clean and no client-traceable strings
  (the distributability gate; authored in the cross-cutting/retrofit node, referenced here).

**Human gates that touch this stage (plain-language names; the gate code is a footnote):**

- **Approve the stage plan** `[G-B]` — approve the drafted brief and its withheld grading key before the
  stage executes. Because the backbone brief is parameterized only by artifacts that exist at the keystone
  (the Snapshot, the future-state graph, the authority envelope), this approval **batches up front**, right
  after the keystone — the backbone is the one design stage that does not need a just-in-time approval.
- **Set the agent's authority boundary** `[G-C]` — ratified once, upstream; it **feeds** this stage (the
  objective, the authority boundary, the retained controls, the handbacks, and the per-surface access
  feasibility). Not decided inside the stage.
- **Sign off the stage** `[G-F]` — clear / accept-with-open / send-back at stage close, as a
  schema-stamped disposition. An accept-with-open lands residuals by kind with a hydrate-at target.

## Session kind — declared interactive

    session_kind: interactive

This stage reads the **live Agent Builder tool**, which needs interactive authentication. It therefore
**cannot run as a spawned subagent**; the orchestrator routes it as an operator-triggered fresh session
(the same human-triggered-relay pattern the existing half uses), never a spawned subagent. The stage
definition declares `session_kind: interactive` so the orchestrator routes it correctly. (This stage
*designs* the reader; it does not itself authenticate the tool.)

## Position on the one event log

The backbone rides the existing generic stage lifecycle on the **one continuous event log** — author the
packet and its withheld grading key, execute in a fresh (interactive) session, independent cross-engine
review, bounded remediation loop, then clear / accept-with-open / send-back. It is a new **stage tag**
(`backbone`), not a new event type.

- Its input arrives as `handoff.internal` (`from_stage: future-state`, `to_stage: backbone`).
- Its `packet.authored` carries `session_kind: interactive`.
- Its `stage.cleared` (or `stage.accepted_with_residual`) binds to the stage's required mechanical check
  set — the structural validator plus the honesty-stamp and scrub checks — re-verified against the latest
  submitted SHA. Passing the independent review is necessary but not sufficient; the mechanical floor
  binds too.

## Visual-quality bar — not applicable

This stage produces **no diagram** (the build-spec spine and the thin index are text/config, not a
rendered graph). The visual-quality north star and the "Check the diagram reads true" gate `[G-D]` do
**not** apply to the backbone. Stated explicitly rather than inventing a diagram: the first diagram-
producing stage is "Derive the agent's internal map", downstream.

## Dry-run result (first-version bar)

The mechanical floor was dry-validated on fixtures (not a live run, not a real Snapshot). The check requires
PyYAML and does a single real structural parse — there is no line-scan fallback to decide differently:

- `checks/check-build-spec-structure.sh` against `tests/knowledge/fixtures/checks/agent-backbone/build-spec.pass.yaml` → **PASS (exit 0)**.
- the same check against `tests/knowledge/fixtures/checks/agent-backbone/build-spec.fail.yaml` → **FAIL (exit 1)**, catching all five
  seeded defects at once: missing `must_not: [all writes]`, a sub-agent decomposition placed in the surface
  block, invented decision rules past the cap, an illegal evidence state, and a `needs_more_information`
  stamp that says nothing about what would resolve it.
- two isolating negative fixtures each **FAIL (exit 1)** on exactly one defect: `build-spec.fail-blocktype.yaml`
  (a required block, `runbook`, typed as a scalar where a mapping is required — the validator type-checks
  every mapping block, not just its presence) and `build-spec.fail-needs.yaml` (a `needs_more_information`
  stamp with neither a `needs:` note nor a `follow_on:` — the missing-resolution honesty check is enforced).
- if PyYAML is unavailable the check exits 2 (setup error) with a clear message rather than degrading.

## Naming

Plain-language names are the primary handle on every surface: the stage ("Build the backbone — the agent's
build-spec spine"), its human gates ("Approve the stage plan", "Set the agent's authority boundary",
"Sign off the stage"), and its outputs (the build-spec spine, the thin index). The internal tag
`backbone`, the gate codes `G-B` / `G-C` / `G-F` are footnotes only.


# Stage — Map the agent's inner team

_A staged SKILL section for the agent-technical-design half of the factory. It mirrors the existing
`within` SKILL stage style; it does not edit that file. Integration and wiring are a
later node. The plain-language stage name is the handle; the bracketed internal tag is a footnote._

Internal stage tag (a footnote, not the handle): `internal-dag`.

## What this stage is

The second stage of the design half. It takes the **built build-spec spine** (the backbone stage's
output) and **activity-timeline evidence** from the Company Brain, and derives the agent's **inner team**:
which sub-agents it decomposes into, how they connect, and the typed interface **surface** each presents.
It produces a second graph object distinct from the workflow graph — the **agent's internal sub-agent
map** — its rendered diagram, and a coverage manifest proving the decomposition covers its whole input
scope.

It maps the inner team; it does not deepen it. It fixes the sub-agents, their connections, the connected
coordinator, and the interface **surface** (typed seams enumerated by direction), and it leaves the
per-sub-agent **interface designs** (typed I/O, the four evidence states, back-signals, failure modes) and
the **decision-policy / rules-as-code** file to the stage that follows — "Deepen each sub-agent". Every
seam carries a `design_ref` pointing at where that deepen work will live, or a stamped `null`.

The map is authored to be **generic and distributable** — no client-, counterparty-, desk-, or real
surface name appears in it. The worked reference is a **de-identified reference agent**, never a named
client's agent.

## Purpose

Produce the agent's inner decomposition so that:

- a downstream stage designer — the deepen stage — can read the sub-agent map and know the sub-agents,
  their connections, and the per-sub-agent interface **surface** to deepen, **without re-deriving what
  this stage produces or where it sits on the pipeline**;
- the decomposition is graded on **covering its input scope**, never on hitting a sub-agent count;
- the agent's authority follows the write stance: **every write edge, write interface and agent-authoritative
  node carries an `authority_grant`** naming its write grant id (a read-only agent has none), decision
  authority no grant backs stays with the policy layer / the review path, and the coordinator is a
  **connected** node that routes and decides only under a write grant;
- every committed value carries a source and an evidence state, and every gap is a stamped open by kind —
  nothing invented.

## Inputs

- the **built build-spec spine** (`build-spec.yaml`) from "Build the backbone" — it declares the sub-agent
  **surface** this stage decomposes and the decision-policy **room** left for the deepen stage;
- **activity-timeline evidence** from the Company Brain (the sequence and shape of the work the inner team
  must cover) — read from on-disk evidence, not a live tool;
- the invariants carried forward (or the note that this is the first version).

Because the brief for this stage SHA-pins the **built** build-spec — an artifact that does not exist until
the backbone stage runs — its plan is approved **just-in-time**, immediately before it executes (see the
event-log position below and §2.3), not batched up front.

## Outputs

- **the agent's internal sub-agent map** (`agents.yaml`) and the **internal-DAG graph object** it renders
  from, from `templates/agents.yaml.template`: node types `{root_agent, step, escalation_gate,
  review_seam}`; edge kinds `{sequence, then, else, handoff, reentry, read_access}` for the sub-agent flow
  plus `dispatch` for the connected coordinator's routing edges; a **connected `root_agent`** carrying its
  dispatch edges; a `coordination_shape` **resolved in the graph** (the un-chosen shapes carry a rejection
  rationale); and the **closed interface set enumerated by direction** (inbound control + outbound handoff
  + reads + draft surface + any granted write or dispatch surface). **Every write carries its write grant**
  (`authority_grant: WG-n`); a read-only agent has no `write` access kind. An `escalation_gate` routes; an
  agent-held decision needs its `authority_grant`;
- **the rendered internal-DAG diagram** (`.graph.json` → HTML + a self-view PNG), from the adapted
  renderer in `renderer/`;
- **the DAG coverage manifest** (`dag-coverage-manifest.md`), from
  `templates/dag-coverage-manifest.template.md` — a **coverage** check over the closed input scope, not a
  count.

## What this stage declares for downstream (its lane)

- It **seats the interface surface** — each seam typed and enumerated by direction, carrying a `design_ref`
  (or a stamped `null`). It does **not** author the per-sub-agent interface designs (typed I/O, evidence
  states, back-signals, failure modes) — that is the deepen stage.
- It **leaves the decision policy to the deepen stage.** A node's `rule_set_ref` is a reference only; this
  stage authors no rule table, no threshold, no decision-policy file.
- Referencing that those exist downstream is required; producing them is out of this stage's lane.

## Release gates

**Mechanical (each must be a re-verified `check.passed`, bound to the latest submitted artifact SHA-256):**

- the **internal-map shape check** — `checks/check-internal-dag-shape.sh` — the internal-DAG stage's own
  mechanical floor: **every write edge or write interface carries an `authority_grant`**; **exactly the declared number of handoffs** (the graph declares
  its handoff count; the check confirms the `handoff` edges match it — a declared-vs-actual
  reconciliation); **an agent-authoritative node only under a write grant** (a node holds decision authority
  only when it carries a non-empty `authority_grant` naming the `WG-n` write grant; otherwise the escalation
  gates are `authoritative: false`, which stays a valid choice); the **root/coordinator is a connected node** (non-zero dispatch edges);
- the **coverage-manifest completeness** check — a **bounded** expectation set graded by coverage of the
  closed input scope, not a per-line stamp and not a target sub-agent count;
- the **vocabulary and client-scrub checks** — FDE-vocabulary-clean and no client-traceable strings (the
  distributability gate; authored in the cross-cutting/retrofit node, referenced here).

**Review-criteria (at the independent cross-engine review, not a script):** the coordinator/root node is a
*connected* node; the coordination shape is *resolved in the graph*, not a provisional spec value; and
every cross-lane interface direction is in scope — reads, outbound handoffs, the draft surface, **and the
inbound control interface** (the direction most often missed).

**Human gates that touch this stage (plain-language names; the gate code is a footnote):**

- **Check the diagram reads true** `[G-D]` — this stage produces a diagram, so a person confirms the render
  is accurate and legible against the render-accuracy checklist (`render-accuracy-checklist.md`), bound to
  the exact rendered-artifact SHA-256. This is a **standing** gate that runs **after** the independent
  review — the reviewer grades structure, not whether the picture reads true; a human eye on the render is
  load-bearing.
- **Approve the stage plan** `[G-B]` — approve the drafted brief and its withheld grading key **just-in-
  time**, immediately before the stage executes (its inputs are a built upstream artifact, so its brief
  cannot be honestly pre-authored).
- **Sign off the stage** `[G-F]` — clear / accept-with-open / send-back at stage close, as a schema-stamped
  disposition. An accept-with-open lands residuals by kind with a hydrate-at target.

## Session kind — declared spawnable

    session_kind: spawnable

This stage reads **on-disk evidence** — the built build-spec and the activity-timeline evidence — and needs
no live tool. So, unlike the backbone stage (which reads the live Agent Builder tool and is declared
`interactive`), it **can run as a spawned subagent**. The stage definition declares `session_kind:
spawnable` so the orchestrator routes it as a fresh spawned executor (§2.1 / §2.3).

## Position on the one event log

The internal-DAG stage rides the existing generic stage lifecycle on the **one continuous event log** —
author the packet and its withheld grading key, execute in a fresh (spawned) session, independent
cross-engine review, bounded remediation loop, then clear / accept-with-open / send-back. It is a new
**stage tag** (`internal-dag`), not a new event type.

- Its input is the **built** build-spec from the backbone stage; its `packet.authored` is instantiated
  just-in-time against that built artifact and carries `session_kind: spawnable`.
- Because it produces a diagram, its `stage.cleared` binds to an **accepted `human.visual_review`** of the
  latest render (SHA-bound) **in addition to** the mechanical check set — the internal-map shape check and
  coverage-manifest completeness re-verified against the latest submitted SHA. Passing the independent
  review is necessary but not sufficient; the mechanical floor and the visual gate bind too.

## Visual-quality bar — applies (this is the first diagram-producing stage)

This stage produces the **first rendered diagram** of the design half, so the visual-quality north star
applies in full. The picture must **read true**, enforced by three things together: the render-accuracy
checklist (`render-accuracy-checklist.md`), the standing "Check the diagram reads true" human gate before
the independent review, and a de-identified **reference render** (`reference/agent-build-example/`) that clears
the proven visual bar. A render that is merely structurally valid but does not read true fails the stage.

## Dry-run result (first-version bar)

The mechanical floor and the reference render were dry-validated (not a live run, not a real Snapshot):

- `checks/check-internal-dag-shape.sh` runs one rule set for every graph (a script, a single agent using
  tools, several sub-agents with or without a coordinator). Against `tests/knowledge/fixtures/checks/internal-dag/internal-dag.pass.yaml` → **PASS (exit 0)**.
- two fixtures pass with a NOTE: `internal-dag.note-zero-edge-root.yaml` (the root carries zero dispatch
  edges: an unfamiliar shape, not broken output) and `internal-dag.pass-write-with-grant.yaml` (a `write`
  edge that states the `authority_grant` naming the ratified decision; the check prints "agent write
  granted").
- five **isolating** negative fixtures each **FAIL (exit 1)** on exactly one defect:
  `internal-dag.fail-write.yaml` (a `write`-kind edge with no stated `authority_grant`),
  `internal-dag.fail-handoff-count.yaml` (declared 2, actual 1), `internal-dag.fail-decision-node.yaml` (an
  agent-owned `authoritative: true` node with no stated `authority_grant`),
  `internal-dag.fail-authoritative-root.yaml` (the `root_agent` is `authoritative: true` with no stated
  grant), and `internal-dag.fail-dangling-dispatch-target.yaml` (a `dispatch` edge points at a node that
  does not exist).
- the same check against the reference **`.graph.json`** → **PASS (exit 0)** — one parse path (PyYAML
  `safe_load`) serves both a `.graph.json` and a YAML `agents.yaml`-shape graph. If PyYAML is unavailable
  the check exits 2 (setup error) with a clear message rather than degrading to a line scan.
- the adapted renderer (`renderer/render_internal_dag.py`) run on the reference graph emitted the diagram
  HTML, standalone SVG and self-view PNG, `agents.yaml` + per-sub-agent configs, and the coverage manifest,
  with every structure check passing. The reference render was checked against the proven visual bar
  (see `README.md`).

## Naming

Plain-language names are the primary handle on every surface: the stage ("Map the agent's inner team"),
its human gates ("Check the diagram reads true", "Approve the stage plan", "Sign off the stage"), and its
outputs (the sub-agent map, the internal-DAG diagram, the coverage manifest). The internal tag
`internal-dag`, the gate codes `G-B` / `G-D` / `G-F` are footnotes only.


# Stage - Design each part's boundary (what crosses it, in and out)

_A staged SKILL section for the agent-technical-design half of the factory. It mirrors the existing
`within` SKILL stage style; it does not edit that file. Integration and wiring are a
later node. The plain-language stage name is the handle; the bracketed internal tag is a footnote._

Internal stage tag (a footnote, not the handle): the interface-design half of `deepen`.

## What this stage is

The first deepen stage. It takes the **built internal map** (the sub-agent map + the internal-DAG graph
object, carrying the **closed interface surface** - every seam typed and enumerated by direction, with
`design_ref: null`) and the **live Company Brain**, and produces **one interface design per sub-agent seam**:
the stable boundary through which each deep module exchanges inputs, outputs, authority signals, and
failures.

It designs the boundary; it does not write the policy. For each seam it fills the typed I/O, the four
evidence states, the back-signals, the authority boundary, and the failure-mode fixture seeds, and it
**publishes** the `<interface-id>.<field>` token set - and it leaves the **decision rules / rules-as-code**
(and the predicate-token binding) to the stage that follows, "Design each part's decision rules". The stage
**freezes** each seam's `seated_in` (never re-seating it) and **fills** its `design_ref` (was `null`).

The design set is authored to be **generic and distributable** - no client-, counterparty-, desk-, or real
surface name appears in it. Any worked reference is a **de-identified reference agent**, never a named
client's agent.

## Purpose

Produce the per-seam boundaries so that:

- a downstream stage designer - the decision-rules stage - can bind its predicates to the **published
  `<interface-id>.<field>` tokens** this stage emits, and the eval-stage designer can seed the test suite
  from the **failure-mode fixture seeds**, **without re-deriving what this stage produces or re-cutting the
  field vocabulary**;
- the agent's authority follows the write stance: every seam carries an **authority boundary** with a
  first-class full stop and `agent_write` — `true` only on a `write` / `outbound_handoff` / `draft` /
  `dispatch` seam naming its `write_grant: WG-n`, `false` otherwise (read-only is a valid choice); no seam authors an escalation trigger or a decision rule;
- every committed element carries a `source` and an `evidence_state` from the canonical four, and every gap
  is a stamped open split by kind - nothing invented.

## Inputs

- the **built internal map** (`agents.yaml` + the internal-DAG graph object) from "Map the agent's inner
  team" - specifically the `closed_interface_set` (every seam typed by `direction`, `seated_in`, `at_step`,
  `design_ref: null`) whose seatings this stage freezes and whose `design_ref` it fills;
- the **live Company Brain** (interactive authentication per session) - the activity-timeline (L4) evidence
  each field's stamp traces to;
- the invariants carried forward (or the note that this is the first version).

Because the brief for this stage SHA-pins the **built** internal map - an artifact that does not exist until
the internal-map stage runs - its plan is approved **just-in-time**, immediately before it executes (see the
event-log position below), not batched up front.

## Outputs

- **the interface-design set** - one `interface-design.yaml` per seam, from
  `templates/interface-design.template.yaml`: the closed **S1-S9** section set and the **four archetypes**
  (read / outbound_handoff / draft_surface / inbound_control, the last with its two-sub-block invocation +
  decision_state shape). **S3 adapts by archetype too**: the template defines each archetype's concrete
  back-signal set - its acknowledgment, its state signal, and its failure back-signal(s) (e.g. read ->
  `read_ack` / `read_state` / `not_found` + `read_structural_failure`; inbound_control -> `invocation_ack`
  / `routing_state_read` / `invocation_incomplete`) - typed and stamped to the same bar as S2, so no
  archetype leaves its S3 shape to runtime. Also: the field-row typing + honesty stamp; the
  `authority_boundary` block with a first-class `full_stop` and `agent_write` (`false` stays valid on every
  direction; `true` only with a `write_grant: WG-n` and a `write`, `outbound_handoff`, `draft` or `dispatch`
  direction, and direction `write` requires it); the
  `boundary_membership` (S9) counterexample-hunt
  records; the `assumptions:` layer; the honesty tail (`residual`); and the S-guard (no rule / threshold /
  trigger). It **publishes** the `<interface-id>.<field>` token set and **fills** each seam's `design_ref`;
- **the per-field evidence sweep manifest** - one row per interface field (the L4 sweep, below);
- **the interface-coverage manifest** (`interface-coverage-manifest.md`), from
  `templates/interface-coverage-manifest.template.md` - a **coverage** check over the closed seam set (one
  design XOR one stamped-open residual per seam), not a count, plus the field-level sweep count.

## The stage's runtime is a guided interview (the client UX)

The rigor of this stage is **produced by a guided flow**, not demanded as fields a client fills and a script
then bounces. A non-technical client is walked through an interview and can follow it without a glossary; the
auditable design (the S1-S9 fields, the stamps, the hunt records) is a **byproduct** the plugin drafts *for*
the client, never something the client authors by hand. The method is authored here and in the brief template;
the build node does not run it.

- **Seam by seam, in plain language.** The plugin walks the client through the built map's closed seam
  surface one seam at a time. For each, it asks plainly what crosses that boundary going in and coming back
  out - "when this part looks at the balance, what does it read, and what does it tell the person back?" -
  and **drafts the structured record from the exchange**: it fills the archetype's S2 inputs and S3
  back-signals, cuts the `<interface-id>.<field>` tokens, and writes the stamps. The client is never asked
  to author YAML or fill a stamp field.
- **The evidence is reached under the hood, not requested as homework.** As the plugin drafts each field it
  reaches the live Company Brain itself - process / step / observation evidence via `get_process_details`
  (scope `tasks`, `observations`, `attributes`) and `get_process_observations` (verbosity `full`) /
  `get_observation_citation`; Company Brain **documents** via `search_artifacts` / `get_artifact_details` /
  `get_artifact_content` **by resource_key**, **not** the process-only `fetch` (which returns "not found" on
  a document key). (These backticked tool names are cited external resources - exempt from the FDE-vocabulary
  ban.) Reaching activity-timeline (L4) evidence means naming an **observation id**, never resting on a step
  title or an AI summary. It stamps each field's `evidence_state` and `source` from what it finds, over the
  **closed** seam set (coverage, never a sample) - one stamped record per S2 field, per inbound_control
  invocation / decision_state field, and per S3 back-signal.
- **The counterexample hunt happens in conversation.** Before a boundary is trusted, the plugin runs the
  live-Brain sweep for the case that would contradict it, then **tells the client what it found** and lets
  them confirm it against how the work really goes: "I looked for a case where this data point is out of
  scope for this part; here's what came back - does that match?" It **stamps the evidence state from that
  exchange** - a claim is only `Observed` once the hunt has run and come back empty or been reconciled with
  the client; an unreconciled contradiction is **downgraded** to `Contradictory` or `needs_more_information`,
  never left `Observed`. The client never populates a `reconciliation_evidence` field; the plugin records the
  re-runnable `search_scope` and the reconciliation evidence into the keyed `boundary_membership` (S9) record
  as the conversation settles it, so an auditor can walk it later.
- **The structural check runs quietly in the background.** The per-design structure check
  (`check-interface-design-structure.sh`) and the set-completeness gate run behind the interview; the client
  never sees a lint dump of predicate codes. A genuine gap surfaces as **one plain sentence** - "the refunds
  boundary has no design yet - shall we do that one?" - not a stack of failed predicates. A field the Brain
  cannot establish is carried as a stamped open in the same plain register ("I couldn't find which field is
  the match key - we'd confirm that with your team"), never guessed to look finished.

The experience is a guided interview that **produces an auditable design as a byproduct**. The rigor - the
four evidence states, the stamping, the no-invention discipline, the grounded boundaries - is preserved; what
changes is that it is **produced by the flow and vouched for by the review**, rather than being a gate the
client must pass field by field.

## Release gates

**Mechanical (each must be a re-verified `check.passed`, bound to the latest submitted artifact SHA-256).
Every check here validates STRUCTURE and CLOSED SETS only - never semantic honesty (see below):**

- the **set-completeness gate** - `checks/check-interface-seating-directions.sh`. This is the stage's
  set-level completeness gate; it **fires at this stage** (per the enforcement model, §2.5 facet 3, bound
  to `stage.cleared` by §4.1's required-check-set rule), told the enumerated closed seam surface from the
  built map (never self-discovering it by globbing designs off disk). It proves per-seam coverage (one design
  XOR one well-formed stamped-open per seam, seating frozen from the built map) **and**, separately, direction
  coverage (each of the four directions has >= 1 seam represented - never "one design per direction"). It is
  distinct from the later cross-stage coherence sink, which reconciles representations against each other;
  set-completeness is owned **here**, not deferred there.
- the **per-design structure check** - `checks/check-interface-design-structure.sh`. **Told a single design**
  as its explicit argument (the orchestrator enumerates the design set from the coverage manifest and hands
  each design in; the check never receives or self-partitions a set, and never self-detects "am I a design").
  It validates content-only structure: required sections present; `evidence_state` / `access_state` values in
  the canonical four (illegal `SOP-documented` fails); a non-empty `evidence_state` **and** `source` present
  on every committed element (a missing stamp key fails); `claim_ref` reference integrity; the authority
  boundary present with a non-empty `full_stop` and `agent_write` set to `false` (read-only, valid on every
  direction) or `true` with a `write_grant: WG-n` on a `write`, `outbound_handoff`, `draft` or `dispatch`
  direction (direction `write` requires `true`); opens split by kind **by placement**
  (access only under `assumptions[]`; a `needs_more_information` field carries a `needs:` note; a rule open is
  a `kind: rule` residual with a `follow_on`, never a field); a hunt record's **well-formedness** (a cleared
  result carries `search_scope` + `reconciliation_evidence`; a `contradictory` result is not stamped
  `Observed`/`Assumed`) - the *shape* of the proof, never whether it is true; the S-guard as a **forbidden-key**
  check (no `rule_sets`/`gates`/`dispatch`/`escalation_triggers`/`thresholds`/`special_cases` block, no
  interim/default/fallback key; `rule_set_ref` is an allowed reference); and the failure-mode fixture *shape*
  (non-empty `synthetic_case` + `expected`, `expected_kind: authority_boundary` only). It carries **no**
  semantic prose classification - no condition->outcome / comparison / ternary sniff over free prose, no
  "is this stamp genuinely grounded" judgment.
- the **house-floor checks** - no-client-traceable-strings, FDE-vocabulary, and plain-language-naming
  (the distributability floor; **authored in the cross-cutting / retrofit node, referenced here** by role,
  with a stage-local dry-run fixture each so the release set is fully self-validatable).

**Semantic honesty is the review gate's job, not the check's (the enforcement model, §2.5 facet 4).** A
passing structural check **never** attests that a design is sound. Whether a stamp is **truly grounded** in
the named Company Brain locus, whether a hunt **genuinely searched** the space it claims, whether a prose
field **smuggles a decision rule** the structure did not catch, and whether the boundary is honestly drawn -
these are judged by the **independent cross-engine reviewer** (against the withheld rubric) and ratified at
**"Sign off the stage"**. The mechanical floor attests structure and closed sets; the review path attests
soundness. A green check is a necessary floor, never a soundness guarantee - stated here so no maintainer or
operator mistakes one for the other.

**Human gates that touch this stage (plain-language names; the gate code is a footnote):**

- **Approve the stage plan** `[G-B]` - approve the drafted brief and its withheld grading key **just-in-time**,
  immediately before the stage executes (its input is the built internal map, so its brief cannot be honestly
  pre-authored).
- **Sign off the stage** `[G-F]` - clear / accept-with-open / send-back at stage close, as a schema-stamped
  disposition. An accept-with-open lands residuals by kind with a hydrate-at target (typically the
  decision-rules stage for a rule open).

**Not seated: "Check the diagram reads true" `[G-D]`.** This stage produces **no diagram** (see below), so it
inherits no visual-review gate and no visual criteria. **"Resolve a flagged blocker" `[G-E]`** fires only if a
check trips downstream; it is not a standing gate of this stage.

## Session kind - declared interactive

    session_kind: interactive

The stage's **runtime** reads the **live Company Brain**, which needs interactive authentication. It therefore
**cannot run as a spawned subagent**; the orchestrator routes it as an operator-triggered fresh session (the
human-triggered-relay pattern), never a spawned subagent. The stage definition declares `session_kind:
interactive` and `packet.authored` carries it.

(Node-topology footnote, not to be conflated with the above: the on-disk **build** of this stage - the node
that produced this staged source - ran as a spawnable executor against the built map + the plan and made no
live-Brain call. The `interactive` declaration is the **stage's runtime** behavior being designed, not the
build node's.)

## Position on the one event log

The interface-design stage rides the existing generic stage lifecycle on the **one continuous event log** -
author the packet and its withheld grading key, execute in a fresh **interactive** session, independent
cross-engine review, bounded remediation loop, then clear / accept-with-open / send-back.

- Its input is the **built** internal map from "Map the agent's inner team"; its `packet.authored` is
  instantiated just-in-time against that built artifact and carries `session_kind: interactive`.
- Its `stage.cleared` (or `stage.accepted_with_residual`) binds to the stage's required mechanical check set -
  the set-completeness gate + the per-design structure check (run per enumerated design) + the house-floor
  checks - re-verified against the latest submitted SHA. Passing the independent review is necessary but not
  sufficient; the mechanical floor binds too. And the reverse holds under the enforcement model: the
  mechanical floor is necessary but never sufficient for **soundness** - it attests structure and closed
  sets, while the independent review + "Sign off the stage" attest semantic honesty.
- **Stage-tag residual — RATIFIED 2026-09-17.** This stage writes the **distinct** `stage` tag
  `deepen-interface-design`. The single `deepen` tag is split into two canonical keys,
  `deepen-interface-design` (this stage) and `deepen-decision-rules` (the decision-rules stage below),
  matching their distinct outputs, checks, and human gates. See
  `scripts/_stageindex.py` / `assets/shipped-canonical-pipeline.v1.yaml`, both updated in lockstep
  (`the stage-index consistency test` asserts they agree). The checks and gates remain
  **tag-agnostic**, so this ratification changes no check's or gate's own logic.

## Visual-quality bar - not applicable

This stage produces **no diagram** (the interface designs and the coverage manifest are text / config, not a
rendered graph). The visual-quality north star and the "Check the diagram reads true" gate `[G-D]` do **not**
apply. Stated explicitly rather than leaving it ambiguous: the diagram-producing stage is "Map the agent's
inner team", upstream.

## Dry-run result (first-version bar)

The mechanical floor was dry-validated on static fixtures (not a live run, not a real Snapshot driven end to
end). Both checks require PyYAML and do a single real structural parse - one `yaml.safe_load` path serving
both a `.yaml` design and a `.graph.json`; there is no line-scan fallback. Full result table in the README.

- the **set-completeness gate** (`checks/check-interface-seating-directions.sh`) against **both** PASS
  fixtures (`interface-seating.pass.yaml` one-seam-per-direction, and `interface-seating-multi-samedir.pass.yaml`
  five seams with two independent `read` seams, plus the four-seam `interface-design.pass.yaml` set) ->
  **PASS (exit 0)**; its eight isolating negatives each **FAIL (exit 1)** on exactly one clause (incl. an
  ill-formed / unstamped stamped-open that does not count, and two stamped opens on one seam).
- the **per-design structure check** (`checks/check-interface-design-structure.sh`) against the single-design
  PASS fixture `interface-design-single.pass.yaml` -> **PASS (exit 0)**; handed the four-seam `designs:` set
  it exits **2** (it takes one design, never a set - the set-partition branch is gone); its **twenty-four**
  isolating negatives (each a COMPLETE design with one injected defect - including the two `fail-malformed-*`
  container-type negatives, where a mis-typed `assumptions` / `residual` container FAILs rather than being
  read as empty and skipped) each **FAIL (exit 1)** on exactly one structural predicate.
- setup errors (missing file; PyYAML absent) exit 2 with a clear message rather than degrading; verdicts are
  cwd-independent.
- the full fixture -> expected-exit -> actual-exit enumeration for **both** checks is committed as an
  inspectable transcript at `checks/fixtures/DRYRUN-RESULTS.md` (produced by actually running the checks).

## Naming

Plain-language names are the primary handle on every surface: the stage ("Design each part's boundary - what
crosses it, in and out"), its human gates ("Approve the stage plan", "Sign off the stage"), and its outputs
(the interface designs, the evidence sweep manifest, the coverage manifest). Each field `name:` label leads
with a plain-language name too. The internal tag (the interface-design half of `deepen`), the gate codes
`G-B` / `G-F`, the seam ids (`IC-1`, `IR-1`) are footnotes only.


# Stage - Design each part's decision rules (rules-as-code)

_A staged SKILL section for the agent-technical-design half of the factory. It mirrors the existing
`within` SKILL stage style; it does not edit that file. Integration and wiring are a later
node. The plain-language stage name is the handle; the bracketed internal tag is a footnote._

Internal stage tag (a footnote, not the handle): the decision-rules half of `deepen`.

## What this stage is

The second deepen stage. It takes the agent's **built interface designs** (each seam's stable boundary,
publishing its `<interface-id>.<field>` tokens) and the **live Company Brain**, and produces the agent's
full **decision policy as rules-as-code**: the rule sets, the recommend-vs-escalate gates, the coordinator's
dispatch guards, the enumerated escalation triggers, the recognize-and-route special cases, a stamped open
register, and the full-stop guardrails - the decision logic that carries **past the Agent Builder
`decision_rules` cap (three-to-eight)**.

It writes the policy; it does not design the boundary. The interface-design stage already fixed the typed
I/O, the four evidence states, the back-signals, and the authority boundary, and **published** the
`<interface-id>.<field>` token set. This stage **binds** its rule predicates to that published token set - it
does **not** re-cut or re-type a field, and it authors no boundary.

The policy is authored to be **generic and distributable** - no client-, counterparty-, desk-, account-,
deal-, or real surface name appears in it. Any worked reference is a **de-identified reference agent**, never
a named client's agent, and it lives outside the shipped bundle (see the README).

## Purpose

Produce the agent's decision policy so that:

- the **eval stage** can seed its test suite from the enumerated **`escalation_triggers`** and **`gates`**
  this stage owns, without re-deriving the condition->escalate policy;
- the agent's authority follows the write stance: every rule set, gate, and dispatch is
  `authoritative: false` (a person on the review path decides) unless it carries `authority_grant` naming the
  write grant id(s) behind it; a decision-bearing system-of-record write is person-routed by default, with
  the two named exceptions (`own-working-surface`; `reversible-reviewed`) and the authority boundary following
  the grant's row in the transformation map's Write grants table (a read-only
  agent keeps every set `false` with no grants); and an authority boundary is a **full-stop `guardrails` statement**,
  never a threshold-gated trigger;
- every committed rule carries `source_ids` and an `evidence_state` from the canonical four, and every gap is
  a stamped open **split by kind** (value / rule / access) - nothing invented, no interim / default / fallback.

## Inputs

- the **built interface designs** from "Design each part's boundary" - the published `<interface-id>.<field>`
  token set (each design's S2 typed inputs, its S2 inbound-control invocation / decision_state, and its S3
  back-signals) that the rule predicates bind to, each token carrying its own evidence stamp and open;
- the **built internal map** (`agents.yaml` + the internal-DAG graph object) - the owners the rule sets seat
  on, the coordination shape, and the write grants (`authority_grant`) its write edges and authoritative nodes carry;
- the **live Company Brain** (interactive authentication per session) - the activity-timeline (L4) evidence
  each rule's condition, action, and any committed value traces to;
- the invariants carried forward (or the note that this is the first version).

Because the brief for this stage SHA-pins the **built** interface designs - an artifact that does not exist
until the interface-design stage runs - its plan is approved **just-in-time**, immediately before it
executes (see the event-log position below), not batched up front.

## Outputs

- **the decision policy** (`decision-policy.yaml`), from `templates/decision-policy.template.yaml`: the
  closed **eight blocks in fixed order** - `provenance · rule_sets · gates · dispatch · escalation_triggers ·
  special_cases · open_register · guardrails` - with the **nine-field-plus-`operative_tokens`** per-rule row.
  It **binds** its predicates to the published tokens (via `operative_tokens`) and authors the
  condition->escalate policy the eval stage consumes;
- **the L4 drilldown sweep record** - one row per rule (and per committed value inside a rule): the named
  expected Brain locus, the retrieval, the canonical-four core-evidence verdict, and `source_ids`;
- **the coverage manifest** (`decision-policy-coverage-manifest.md`), from
  `templates/decision-policy-coverage-manifest.template.md` - the per-owner rule-set roll-up, the frozen
  stress-test set + the set-aside record, the per-rule hunt records, and the binding-gate reconciliation
  line. Mechanically countable;
- **the binding manifest** - generated by the binding gate (never hand-edited).

## The live-Brain L4 drilldown method (the stage's runtime)

The stage runs an **interactive live-Company-Brain session**. It grounds each rule's condition, action, and
any committed value at **activity-timeline (L4)** evidence - not a shallow pull. The method is a
**reproducible procedure a fresh interactive session follows**, over the **closed** rule set (coverage, never
a sample):

1. **Name the expected Brain locus.** State which process step / observation the rule's evidence should live
   on. A rule with no named expected locus cannot be swept.
2. **Reach the locus under the hood.** Retrieve it from the live Brain - process / step / observation
   evidence via `get_process_details` (scope `tasks`, `observations`, `attributes`) and
   `get_process_observations` (verbosity `full`) / `get_observation_citation`; Company Brain **documents** via
   `search_artifacts` / `get_artifact_details` / `get_artifact_content` **by resource_key** (not the
   process-only `fetch`, which returns "not found" on a document key). (These backticked tool names are cited
   external resources - exempt from the FDE-vocabulary ban.) Reaching **L4** means naming an **observation
   id**, never resting on a step title or an AI summary.
3. **Record the core-evidence verdict**, mapped to the canonical four: **Observed** (the locus grounds the
   condition + action + any committed value; `source_ids` names it), **Assumed** (a proposed policy or a
   future-state construct the timeline does not directly observe - e.g. a dispatch act between lanes - stamped
   `Assumed`, never presented as `Observed`), **Contradictory** (the document and the observation conflict on
   the rule's basis; both sources named; the rule becomes a register item - never "intended vs actual"), or
   **needs_more_information** (the Brain cannot source the rule's committed value - a **value open**, never
   fabricated).
4. **Route a policy gap to the register.** Where the gap is a *rule / precedence / threshold not yet decided*
   (not a missing data value), it becomes an `open_register` **residual** (`kind: rule`) with a named
   `follow_on` + `closure_route` - never a fabricated rule row, never a default.

**Done-condition:** every rule (and every committed value inside it) carries a core-evidence verdict from the
sweep, with a named locus and `source_ids`; nothing committed is unstamped and nothing unsourced is asserted.

## The counterexample hunt + the "Choose which rules to stress-test" freeze

For a generic, client-distributable stage the hunt is **risk-tiered with a human-frozen set** - not a static
tiering the stage hard-codes, and not a hunt-everything mandate. It is a **standardized, fixed-form in-stage
human decision point**, positioned once: **after the L4 drilldown has produced the drafted rule set and
before any rule is trusted.**

**The freeze decision point - plain name: "Choose which rules to stress-test."**

1. **Present the drafted decision rule set to the user** in plain language (the per-part rule sets; never a
   lint dump of rule ids).
2. **Present suggestions on which rules most need stress-testing.** The stated risk heuristic ranks highest
   the rules touching: **authority boundaries; escalation; opens (`needs_more_information` / `Contradictory`);
   precedence / tie-breaks; and high-impact actions.** The suggestion is a recommendation the user acts on,
   not an auto-selection.
3. **Work with the user to FREEZE the hunt set** - the rules the counterexample hunt covers this run. The
   frozen decision is recorded (the `frozen_hunt_set` record in the coverage manifest).
4. **Record every excluded rule** with its ratified exclusion - an auditable "excluded by ratification at the
   freeze gate" note (the `excluded_rules` record). **Never silently skipped.**

**The gated per-rule hunt (for each rule in the frozen set):** actively hunt, against the live Brain, the
case that **breaks the rule** - the boundary / threshold / precedence it gets wrong. The rule is **not
trusted** until the hunt runs **empty** or the counterexample is **reconciled**; until then its
`evidence_state` is no better than `needs_more_information` (a contradictory result downgrades it to
`Contradictory` / `needs_more_information`, never left `Observed` / `Assumed`). Record an auditable,
re-runnable per-rule hunt record: `search_scope · result {empty | reconciled | contradictory} ·
reconciliation_evidence` (for `empty`, the named clean scope; for `reconciled`, how it was reconciled + the
source id(s); for `contradictory`, the conflict).

The freeze point is a **within-stage standardized decision** whose auditable outputs (the frozen set + the
exclusions) are captured as **first-class stage artifacts** in the coverage manifest and **verified by
`check-hunt-coverage.sh`** - it does **not** emit a seventh top-level control-plane gate (the run's thesis is
a six-gate control plane, everything else mechanized or standardized in form). This mirrors the
interface-design stage's guided-interview seam-by-seam confirmations.

**Reconciliation against the human-input model (why this is not a seventh gate and not a detector-fired
exception).** The freeze is a **stratum-2 in-stage decision** - a run-specific, standardized typed input
folded into the stage's runtime method - **not** a top-level control-plane gate and **not** a detector-fired
exception:

- **Not a seventh top-level gate.** The freeze point emits **no** top-level `human.decision.gate` event. Its
  standardization and auditability come from the **fixed-form artifact + the check** (the `frozen_hunt_set` and
  `excluded_rules` records, captured as data and bound to `stage.cleared` via the required-check-set rule), not
  from a gate event. Adding a seventh gate would over-count the control plane against the six-gate thesis.
- **Not "Resolve a flagged blocker" / `mid-execution-steer`.** That gate is **detector-fired** (non-convergence,
  unbindable-predicate, coherence-residual) - a *structured exception*. The freeze is the opposite: a
  **planned, positioned, every-run** decision. Typing it as `mid-execution-steer` would misrepresent a planned
  step as a steer/exception, require minting a new `trigger` enum value, and over-count the control plane.
  `mid-execution-steer` is the **closest existing value** *if* a typed provenance event were ever required, and
  is named as such - but the stage explicitly **declines to emit it**, keeping the freeze a within-stage
  standardized decision whose record is a stage artifact.
- **Where the human-input model places it.** The model's frame maps the freeze to a **stratum-2 (run-specific
  typed input) standardized and folded into the stage's runtime method** - the standard-form input captured as
  **data** (the frozen-set + exclusions record), not a new gate. This satisfies "standardized, positioned at a
  known point, minimized" **without expanding the six-gate surface.**

## Release gates

**Mechanical (each must be a re-verified `check.passed`, bound to the latest submitted artifact SHA-256).
Every check here validates STRUCTURE and CLOSED TOKEN SETS only - never semantic honesty (see below):**

- the **predicate-binding gate** - `checks/check-predicate-binding.sh` (FM-16, the load-bearing release
  check). **Told** two explicit arguments (never globbing): the published interface-token index and the
  decision policy. It parses every rule condition, **extracts** each operative token by a fixed
  index-independent grammar (a malformed token-like span is rejected, never truncated to a published-looking
  prefix), **reconciles** the extracted set against the rule's declared `operative_tokens` (they must be
  equal), and **matches** each token against the index by **exact membership only** (no prefix/parent
  fallback - a published parent never binds an unpublished subfield). A single UNBOUND predicate **or** a
  single reconciliation defect stops the gate. The **only two ratified UNBOUND resolutions are human-decided**
  (this is "Resolve a flagged blocker"): **(a) extend the owning interface** with a *grounded* derived signal
  (routed back through the interface-design stage and re-derived into the token index), or **(b) drop the
  predicate.** No token is invented; no rule's meaning is bent to fit an existing token.
- the **rule honesty-stamp check** - `checks/check-rule-honesty-stamp.sh`. **Told** the decision policy and
  the token index. Validates stamping presence (canonical-four `evidence_state`, non-empty
  `source_ids` / `company_brain_source`, non-empty `operative_tokens`; the illegal `SOP-documented` fails),
  opens split by kind by **placement** (value open <-> `kind: value` register entry; a `kind: rule` open has a
  `follow_on` + `closure_route`), **inherited-open stamping** (a rule binding an index-open token carries an
  `open_stamp` naming a present register entry), access as a **closed forbidden-key set** (access lives only
  as an Assumed `kind: access` register entry, never inside a rule row), no `default` / `interim` / `fallback`
  key, and `authoritative: false` on every rule set, gate and dispatch unless that same object carries an
  `authority_grant` naming one or more `WG-n` write grants (`false` with no grant stays valid).
- the **hunt-coverage check** - `checks/check-hunt-coverage.sh`. **Told** the decision policy and the coverage
  manifest. Proves the `frozen_hunt_set` decision is recorded, every policy rule is accounted for as frozen
  XOR excluded-with-a-ratified-note, and every frozen rule's hunt record cleared (`empty` / `reconciled` with
  **both** a non-empty `search_scope` **and** a non-empty `reconciliation_evidence`) - a `contradictory`,
  missing, or evidence-incomplete record fails.
- the **house-floor checks** - no-client-traceable-strings, FDE-vocabulary, and plain-language-naming (the
  distributability floor; **authored in the cross-cutting / retrofit node, referenced here** by role, with a
  stage-local dry-run fixture each so the release set is fully self-validatable).

These checks are **designed once against the §2.5 model and consolidated - no adversarially-accreted
script** (the FM-2 guardrail); each validates structure + closed token sets only.

**Semantic honesty is the review gate's job, not the check's (the enforcement model, §2.5 facet 4).** A
passing structural check **never** attests that a policy is sound. Whether a rule is **right**, whether a
stamp is **truly grounded** in its named Company Brain locus, whether the hunt **genuinely searched** the
space its `search_scope` claims, whether a full-stop is honestly a boundary (vs a smuggled conditional), and
whether the right token was chosen for a rule's meaning - these are judged by the **independent cross-engine
reviewer** (against the withheld rubric) and ratified at **"Sign off the stage"**. **A passing structural
check is never, on its own, evidence that a policy is sound** - stated here so no maintainer or operator
mistakes one for the other.

**Human gates that touch this stage (plain-language names; the gate code is a footnote):**

- **Approve the stage plan** `[G-B]` - approve the drafted brief and its withheld grading key **just-in-time**,
  immediately before the stage executes (its input is the built interface designs, so its brief cannot be
  honestly pre-authored).
- **Sign off the stage** `[G-F]` - clear / accept-with-open / send-back at stage close, as a schema-stamped
  disposition. An accept-with-open lands residuals by kind with a hydrate-at target (a value open awaiting the
  client; a rule open with its `follow_on`) - but **never** lands a known-unsound mechanism (an UNBOUND
  predicate is a blocker resolved at "Resolve a flagged blocker", never carried as a residual).

**Not seated: "Check the diagram reads true" `[G-D]`.** This stage produces **no diagram** (its outputs are
config / text). It inherits no visual-review gate and no visual criteria.

**"Resolve a flagged blocker" `[G-E]`** fires **only** when a check trips - specifically an **UNBOUND
predicate** (the binding gate stopping). The decision **is** the choice between the two ratified resolutions
above; it maps onto the existing `human.decision.gate` value `mid-execution-steer` with `trigger:
unbindable-predicate` (§3.2 / §4.1) - no new gate.

## Session kind - declared interactive

    session_kind: interactive

The stage's **runtime** reads the **live Company Brain**, which needs interactive authentication. It therefore
**cannot run as a spawned subagent**; the orchestrator routes it as an operator-triggered fresh session (the
human-triggered-relay pattern). The stage definition declares `session_kind: interactive` and
`packet.authored` carries it.

(Node-topology footnote, not to be conflated with the above: the on-disk **build** of this stage - the node
that produced this staged source - ran as a spawnable executor against the built interface designs + the
plan and made no live-Brain call. The `interactive` declaration is the **stage's runtime** behavior being
designed, not the build node's.)

## Position on the one event log

The decision-rules stage rides the existing generic stage lifecycle on the **one continuous event log** -
author the packet and its withheld grading key, execute in a fresh **interactive** session, independent
cross-engine review, bounded remediation loop, then clear / accept-with-open / send-back.

- Its input is the **built** interface designs; its `packet.authored` is instantiated just-in-time against
  that built artifact and carries `session_kind: interactive`.
- Its `stage.cleared` (or `stage.accepted_with_residual`) binds to the stage's required mechanical check set -
  the predicate-binding gate + the rule honesty-stamp check + the hunt-coverage check + the house-floor
  checks - re-verified against the latest submitted SHA. Passing the independent review is necessary but not
  sufficient; the mechanical floor binds too. And the reverse holds: the mechanical floor is necessary but
  never sufficient for **soundness**.
- **Stage-tag residual — RATIFIED 2026-09-17.** This stage writes the **distinct** `stage` tag
  `deepen-decision-rules`, paired with `deepen-interface-design` on the interface-design stage above.
  See `scripts/_stageindex.py` / `assets/shipped-canonical-pipeline.v1.yaml`. The checks and gates
  remain **tag-agnostic**.

## Visual-quality bar - not applicable

This stage produces **no diagram** (the decision policy and the coverage / binding manifests are text /
config, not a rendered graph). The visual-quality north star and the "Check the diagram reads true" gate
`[G-D]` do **not** apply. Stated explicitly rather than leaving it ambiguous.

## Dry-run result (first-version bar)

The mechanical floor was dry-validated on static fixtures (not a live run, not a real Snapshot driven end to
end). All three checks require PyYAML and do a single real structural parse - one `yaml.safe_load` path; there
is no line-scan fallback (PyYAML absent -> exit 2). Verified cwd-independent. The full fixture -> expected-exit
-> actual-exit enumeration for all three checks is committed as an inspectable transcript at
`checks/fixtures/DRYRUN-RESULTS.md` (produced by actually running the checks). Summary in the README.

## Naming

Plain-language names are the primary handle on every surface: the stage ("Design each part's decision rules"),
its human gates ("Approve the stage plan", "Sign off the stage", "Resolve a flagged blocker"), the freeze
decision point ("Choose which rules to stress-test"), its outputs (the decision rules, the evidence-sweep
record, the coverage manifest, the binding manifest), and **every template field** (per the plan §2.4 label
mapping - each field's plain-language label leads; the schema id is the on-disk key, a footnote). The internal
tag (the decision-rules half of `deepen`), the gate codes, and the rule ids (`r-001`, `disp-01`) are
footnotes only.


# Stage — Build the test suite and check the whole design fits together

_A staged SKILL section for the eval half of the agent-technical-design factory. It mirrors the existing
`within` SKILL stage style; it does not edit that file. Integration and wiring are a later
node. The plain-language stage name is the handle; the bracketed internal tag is a footnote._

Internal stage tag (a footnote, not the handle): `eval`.

## What this stage is

The plugin's generic **eval stage**. It turns an agent's **built decision policy** (its
`escalation_triggers` + `gates`), its **built interface designs** (their published `<interface-id>.<field>`
tokens + per-sub-agent `failure_modes`), and its **built internal map** (the judged-gate topology) into two
things:

- a **test suite** — candidate eval cases, gate-scoped, plus a numeric-floor calibration stamped **open**;
- an **automated-judge instrument** — a judge design, judge rubrics, a verdict schema, a calibration set,
  and a runner (`run-judge.py`) that is **smoke-provable** before it is trusted.

The stage is built **once, generically**. It does **not** fan out into any specific agent's suite and does
**not** author any specific agent's eval cases. Every worked artifact is a de-identified reference agent —
no client, counterparty, desk, real threshold, or real surface name appears anywhere.

## Purpose

Produce an eval instrument that:

- **covers** every escalate-if rule the decision policy enumerates and every judged gate the internal map
  produces — coverage, not a fixed count;
- **judges** an agent output only after it clears a deterministic hard gate, with a genuinely independent
  cross-engine reviewer, one anchored question at a time, against the decision policy — never an all-knowing outside source;
- **is honest** — every case stamped, the numeric acceptance floor stamped **open** with a stated
  derivation method, no fabricated aggregate safety rate anywhere;
- **proves itself** — the checks and the judge run end to end on synthetic fixtures, with a committed real
  Codex calibration transcript, before the stage can clear.

## Inputs

- the **built decision policy** — `rule_sets[].rules[].rule_id`, `gates[].gate_id` +
  `forced_escalate_if_any`, `escalation_triggers[]` (the escalate-if set the coverage check enumerates + the
  `policy_ref` membership index);
- the **built interface designs** — the published token surface (S2 typed inputs, S2 `control_inputs`
  invocation/decision_state, S3 `outputs_back_signals`, each `token: "<interface-id>.<field>"`), the
  per-sub-agent `failure_modes[]` (the calibration keys), and the S7 `eval_hooks` (the
  Layer-1 engine input);
- the **built internal map** — the judged-gate topology the coverage check derives from, and the
  invariants to preserve (every write and agent-held decision carries its write grant; escalation gates
  `authoritative: false` unless granted);
- the **backbone** — the house SKILL-section + templates + checks + control-templates layout.

The runtime consumes these **on disk** — **no live Company Brain and no live agent**. The one model call
anywhere in scope is the judge's own Codex subprocess during the calibration smoke.

## Outputs

- **`eval-stage/templates/eval-set.template.yaml`** — the closed generic gate-scoped test-suite skeleton; loads cleanly
  through `framework/evals/eval_set_consumer.load_eval_set`.
- **`eval-stage/templates/judge-rubrics.template.yaml`** — the anchored judge rubrics the runner feeds the judge
  (`pass_bar: 4`; each run one anchored 0–5 criterion + `critical` flag + a verbatim `policy_excerpt`).
- **`eval-stage/templates/calibration-set.template.yaml`** — the synthetic calibration set (`correct`/`flawed` mocks;
  each flawed mock plants one defect keyed to a declared interface `failure_mode`).
- **`eval-stage/templates/verdict-schema.json`** — the per-gate verdict schema (inherited verbatim; `run_id` an open
  string).
- **`eval-stage/templates/eval-set-coverage-manifest.template.md`** — the human-readable roll-up of the two coverage
  manifests.
- **`eval-stage/scripts/run-judge.py`** — the calibration meta-eval runner. It routes each mock's structured output
  through the Layer-1 `CheckEngine.from_interface_defs(...).all_pass(...)` hard gate FIRST, then scores the
  check-passed judged-gate outputs through `framework/evals/judge.JudgeSeam` (the `codex exec` call injected
  as its `runner`; it never bypasses `JudgeSeam` to call Codex directly), loading the eval set through
  `load_eval_set`. `--dry-run` validates the Layer-1 gate + the JudgeSeam/prompt/rubric/schema/polarity
  wiring with no model call.
- **`eval-stage/scripts/check-routing-seam.py`** — the deterministic routing/dispatch code-check.
- **`eval-stage/checks/check-case-coverage.sh`**, **`eval-stage/checks/check-case-honesty-stamp.sh`**,
  **`eval-stage/checks/check-judge-instrument-smoke.sh`** — the three release checks.
- **`checks/fixtures/…`** — de-identified PASS + isolated-defect FAIL fixtures + the committed
  **`DRYRUN-RESULTS.md`** and the real Codex calibration transcript.
- **`tests/knowledge/control-templates/eval-suite-runtime-rubric.template.md`** — the stage's **withheld** runtime review
  rubric (the firewall — never co-located with the product rubrics in `templates/`).

## The three-layer judge stack — built ON the existing `framework/evals/` seams

The stage does **not** reinvent the eval machinery; it wires the built artifacts into the eval seams
that ship under `framework/evals/`. What ships is deliberately minimal — `engine.py`, `judge.py`, and
`eval_set_consumer.py`; the finer-grained seams named below (a separate per-validator-kind module and a
tracing seam) are **named as design, not shipped**, and the prose marks each as such.

- **Layer 1 — deterministic code validators (the hard gate, runs FIRST).** Built on
  `framework/evals/checks/engine.py` (`CheckEngine.from_interface_defs(interface_def_paths)` →
  `all_pass(output)`). As shipped, `engine.py` validates inline: it checks that every REQUIRED input token
  a seam declares (its S2 `inputs.fields` rows and, for an inbound-control seam, its `control_inputs`
  `invocation` / `decision_state` rows) is carried in the output, and that each carried back-signal is well-shaped and a declared
  token of the seam, failing closed on an unknown seam or one with zero applicable checks. The broader
  validator-kind taxonomy the design calls for (field, grouping, back-signal-shape, enum, escalation-first,
  join-honesty, draft-only, and evidence-state validation) is **described as design; only field-presence and
  back-signal-shape validation ship in `engine.py` today** — there is no separate validator-kinds or results
  module. The engine reads the built interface designs' S7 `eval_hooks` and builds the
  concrete check instances — tokens, allowed sets, and required fields all arrive from the interface YAML.
  **No client token is hardcoded.** The judge runs **only on check-passed outputs**: `all_pass`
  fails closed on an unknown seam or zero applicable checks, so an unvalidated output can never appear
  gate-passed. The scripted dispatch seam is **code-checked, not judged** — `eval-stage/scripts/check-routing-seam.py`.
- **Layer 2 — the independent LLM rubric judge.** Built on `framework/evals/judge.py`'s `JudgeSeam`
  (`JudgeSeam(judge_model, judged_gate_ids, runner)` — routes only `judged: true` gates via `is_gate_judged`,
  scores only check-passed outputs; the `judged_gate_ids` come from `EvalSet.judged_gates()`; the injected
  `runner` is the Codex subprocess; the default mock runner never fabricates a score). Decomposed into
  tightly-scoped **single-question** runs, each one anchored 0–5 criterion, `pass_bar: 4`, a below-bar
  **critical** run blocks, rubric-before-output, each carrying a verbatim `policy_excerpt` (the owning
  decision-policy rule row only). Fixture-grounded runs cite the canonical fixture as ground truth, injected
  as a **separate** block, never as an answer key. The delivered judge runs on a **different model family
  than the agent under test** (Codex, via `codex exec`); the model is declared but swappable
  (`judge_model` is config data), and the specific production model is a stamped open.
- **Layer 3 — human gold.** **Deferred** to the sandbox/pilot (`gold_adjudicated: false`) — named as the
  capability→regression graduation path, **not built**.

**The correct-answer source is the decision policy, never an all-knowing outside source; a fixture is ground truth of which
rows/records exist, never an answer key.** State this plainly wherever the instrument is described.

## The two-dimensional coverage model

The case-coverage check's closed set is **two-dimensional**, and it grades **coverage, not a fixed count**:

- **Policy coverage** — the enumerated escalate-if rule set (`escalation_triggers[]` + any
  `gates[].forced_escalate_if_any`). Every escalate-if rule that is **adjudicable pre-pilot** has ≥1 positive
  case + ≥1 paired must-escalate negative (a `required: true` case whose `policy_ref[]` names the rule). A
  rule whose behaviour is **not adjudicable until an open closes** is recorded in `meta.uncovered_open_rules`
  with its `open_stamp` — **never a silent skip**. An escalate-if rule that is neither case-instantiated nor
  ratified-uncovered **fails the check**.
- **Topology coverage** — the judged-gate set derived from the built internal map (every `kind: agent`
  sub-agent's terminal outbound handoff; a chained output adds an inter-stage propagation point on the
  emitting gate). Every judged gate has ≥1 case + ≥1 scoped judge run.

The check takes the eval set, the decision policy, the internal-map graph, and the judge rubrics **as
explicit arguments** and never sniffs a directory. It validates **structure + closed sets only**.

## The per-case must-escalate safety invariant + the stamped-open numeric floor

- **Per-case hard rule.** Each must-escalate case carries `required: true`. The judge's escalation-soundness
  run for that case is **critical**; if the agent acts instead of escalating, that run scores **0**, the run
  is critical, the runner re-derives `escalate`, and clearance is **blocked**. Enforced case by case.
- **No fabricated aggregate.** No aggregate safety-rate number is authored. A design-time `<5%`-style figure
  with no pilot behind it is banned. The population-level safety rate is a stamped `numeric_floor: open`.
- **The derivation method (so the floor is honestly closeable later).** The floor is the
  **false-positive-class rate** — the rate at which the agent *acts where it should have escalated* —
  computed over an **adjudicated pilot/sandbox population** at each judged gate, ratified with the client.
  The method is carried in `meta.numeric_floor_method`; the number stays open.
- **Capability-at-k and the confusion matrix** are the post-sandbox measurement layer — named as the
  graduation path, **not built** (an agent-capability result needs a runnable agent, which does not exist
  before the sandbox).
  Stage 12's Simulation run is not that population; it is readiness evidence, and neither is the pre-build simulation.

## The release gates (this stage seats exactly two)

- **"Approve the stage plan"** — approves the drafted executor packet + the withheld grading key, and
  ratifies the coverage manifest **including `uncovered_open_rules`**, so the intentionally-uncovered
  triggers are an auditable ratified exclusion, not a silent skip.
- **"Sign off the stage"** — `clear` / `accept-with-open` / `send-back`. The numeric floor stamped open
  lands here as an **accept-with-open residual** (kind: value, hydrate at the sandbox/pilot), not a new gate.
- **"Check the diagram reads true" does NOT apply** — this stage produces **no diagram**. The
  visual-quality bar is **N/A**.

### Why "Resolve a flagged blocker" does NOT fire for this stage

This stage carries two mechanical blockers, each handled **mechanically in-stage first**. The
architecture-owned exception-trigger enum has since grown (membership-only) to name the escalation trigger
for each, so the full enum is now `{non-convergence, unbindable-predicate, coherence-residual,
eval-instrument-failure, uncovered-escalation-trigger}`. A blocker reaches the top-level "Resolve a flagged
blocker" gate (G-E) on its named trigger only when the mechanical in-stage resolution below cannot clear it;
no seventh gate is added:

- **A judge-instrument smoke failure is a mechanical check failure.** `check-judge-instrument-smoke.sh`
  exits non-zero — the routing-seam code-check or the calibration polarity map did not hold. That failed
  `check.passed` blocks `stage.cleared` via the required-check-set binding. The resolution needs **no human
  steer**: the executor **fixes the instrument and re-runs** the smoke until it exits 0. A smoke failure
  lands `send-back`, never accept-with-open — **accept-with-open may never land a component whose own
  enforcement is known unsound**.
- **An uncovered escalation trigger that is not a ratified exclusion blocks the coverage check.**
  `check-case-coverage.sh` fails on any escalate-if rule that is neither case-instantiated nor listed in
  `meta.uncovered_open_rules`. **Ratifying an exclusion** is an in-stage standardized decision point —
  **"Choose which escalate-if rules stay uncovered this run"** — in the same fixed-form shape as the
  decision-rules stage's hunt-set freeze: the ratified decision is captured as a first-class stage artifact
  (the `uncovered_open_rules` record), verified by `check-case-coverage.sh`, and bound to `stage.cleared`.
  It emits **no** top-level `human.decision.gate` event.

Whether the architecture's exception vocabulary should grow a named value for an eval-stage condition was
an architecture-owned open this stage carried; it has since been **resolved at the architecture layer** — the
exception-trigger enum grew (membership-only) to add `eval-instrument-failure` and `uncovered-escalation-trigger`,
the escalation triggers for the two blockers above. The stage still handles each mechanically first and
escalates on the named trigger only when it cannot.

## The §2.5 enforcement model — what a green check does and does not attest

The design set (the judged gates + the escalate-if rules) is **enumerated and passed to each check as
arguments**, never sniffed. Per-file checks validate **structure + closed sets only**. Set-completeness
(both coverage dimensions) is this stage's first-class gate. Checks are **designed once against this model
and consolidated** — no adversarially-accreted script; a maintainer can read each check and see why each
clause exists.

**In plain words: semantic honesty — whether the cases are adequate and the judge genuinely independent — is
the independent review's and the human sign-off's job, never a check's.** A green case-coverage check counts
coverage; it cannot tell whether a case truly exercises its trigger. A green honesty-stamp check confirms
membership and stamp shape; it cannot tell whether a `grounding` is on-point. **No green check attests
soundness.** That soundness is exactly what the withheld runtime rubric
(`tests/knowledge/control-templates/eval-suite-runtime-rubric.template.md`) has the independent reviewer decide.

**The firewall.** The judge rubrics in `templates/` are a **product** the stage ships — executor-facing,
because the runtime needs them to run the judge — and are therefore **not a withheld grading key**. The
firewall applies only to the stage's **own** runtime review rubric, which lives under `tests/knowledge/control-templates/`,
never co-located with the product rubrics.

## Position on the one event log · session topology

The stage lands its `check.passed` events and its `human.decision.gate` events on the **one factory event
log**, exactly as the prior stages do. The runtime stage declares **`session_kind: spawnable`** — it
consumes the on-disk built decision policy, interface designs, and internal map (no live Company Brain, no
live agent), which distinguishes it from the interactive deepen stages. This plan and the execute node that
builds the stage are likewise spawnable; the orchestrator may run the stage as a spawned subagent.

## Out of scope for the eval stage (named, not built here)

- **The mock→real-agent judge wiring** — the flip from synthetic calibration mocks to a live agent's
  outputs. `framework/evals/eval_set_consumer.py` already scopes this out (`UnwiredEvalSetConsumer` raises
  rather than fabricate a verdict); the eval stage inherits that boundary and does not cross it.
- **The real numeric acceptance floor** — needs labelled pilot/sandbox data; `numeric_floor: open`.
- **Capability-at-k / the confusion matrix** — the post-sandbox measurement layer; tracing is wired at stage 12
  (the observability capability, default Phoenix), not here.
- **Human gold adjudication** — Layer 3; `gold_adjudicated: false`; the capability→regression graduation
  path.
- **The cross-cutting house-floor scrub / FDE-vocabulary / plain-language-naming SCRIPTS** — a later node's;
  this stage wires them into its release gate **by role** and its own artifacts must pass them; it does not
  re-author them.

## Check dry-run results

The three checks + the judge instrument were run end to end over the de-identified reference fixtures; every
real exit code is committed in **`checks/fixtures/DRYRUN-RESULTS.md`** (including one **real Codex**
calibration smoke that cleared with matching polarity, and a negative control that exits 1). A judge-
instrument smoke failure is a blocker to fix and re-run, not a residual to carry.


# Gate — Check the whole design tells the same story (closes stage 10)

_A staged SKILL section for the agent-technical-design half of the factory. It mirrors the existing
`within` SKILL section style; it does not edit that file. Integration and wiring are a later
node. The plain-language gate name is the handle; the bracketed internal tag is a footnote._

Event-log stage tag (a footnote, not the handle): `design-coherence`, a gate tag. Since 2026-09-25 coherence is
not a pipeline stage: it is one re-runnable gate that closes stage 10 (eval). It has no ordinal, never takes a
terminal event, and its pass is derived from its checks and its review (`scripts/_coherence_gate.py`). Older
logs may carry the legacy stage key `coherence-loop`. Where this section says "stage", it names a real pipeline
stage, never the gate.

## What the gate is

The gate that closes the design half's stage 10. It takes an agent's **built design set** on disk — the backbone
build-spec spine, the internal map, the interface designs, the decision policy, the internal-DAG
diagram, and stage 10's Test Suite — and drives it to **coherence**: it proves that all six representations reconstruct **the same
agent**, or names exactly where they diverge and drives each divergence to closure through the stage that
owns the diverging artifact.

It is a **bounded drive-to-coherence loop, not a report-only checker.** Its done-criterion is **coherence
achieved (no standing inconsistency) OR every residual legitimately escalated as a named open** — never
"the reconciliation ran." A cleared coherence matrix is a valid **intermediate**, not the gate being done.

It reconciles the six representations **against each other.** It never re-owns whether one artifact's own set
is complete (that fired at that artifact's owning stage), and it never judges whether a representation is
semantically *good* — that is the independent cross-engine review's and the human sign-off's job.

## What the gate is NOT

- It **never edits another representation in place.** Every correction re-enters the owning artifact's stage
  (its executor brief + rubric + independent Codex review), so localization and independent review survive.
- It reconciles a **client agent's** built design set. It does **not** reconcile the plugin's own source
  (that is the integration stage's end-to-end pass).
- It **authors no diagram** — it consumes the internal-DAG diagram as an input. So the visual review gate
  ("Check the diagram reads true") does **not** apply here.

## Inputs (all on disk — no live Company Brain, no live agent)

Every input is pinned by path and sha256 in the **design set**, `output/coherence/design-set.json`, written by
`scripts/coherence-design-set.py --run-root DIR --write`: the latest submitted artifact (and its companion files)
of each **cleared** stage among 1–9, and stage 10's latest **submitted** Test Suite whose review returned clear
(stage 10 is not cleared yet when the gate first runs, and must not be: its clearance waits on the gate). The
script refuses to pin a half-finished design (a stage among 1–9 reopened and not re-cleared, or stage 10 with no
clear review). Stages a seeded run did not run are pinned from the brought files its join marker names.

- the **built backbone** — `build-spec.yaml` + the `AGENTS.md` thin index;
- the **built internal map** — `agents.yaml` + `agents/sa-*.yaml` + the internal-DAG **graph object**
  (`*.graph.json`, the authored source of truth) + `dag-coverage-manifest.md`;
- the **built interface designs** — `interface-design/*.yaml` (one per seam) + the published
  `<interface-id>.<field>` token index;
- the **built decision policy** — `decision-policy.yaml` + its binding manifest;
- the **rendered internal-DAG diagram** — `*-internal-dag-diagram.html` / SVG;
- the **Test Suite** — stage 10's eval set, judge rubrics, calibration set and coverage manifest (now a reconciled
  representation: nothing else re-checks the suite against the design after stage 10);
- the transformation map's **Write grants table** (the write-grant register, the shape source-of-record for
  INV-10, matched by grant id) and the other upstream pins
  (snapshot, workflows) the design points at (INV-15);
- the invariants carried forward, and any escalated residuals from an earlier pass.

The gate's pass is valid only while every pinned file is unchanged on disk: `checks/check-design-set-current.sh`
re-hashes them, and the writer re-runs it when a stage that requires the gate clears or starts.

## Outputs

- the **coherence matrix** (one per agent; `templates/coherence-matrix.template.md`);
- the **findings register** (`templates/findings-register.template.md`);
- the **reconciliation note** (`templates/reconciliation-note.template.md`);
- the supporting **disambiguation register** (`templates/disambiguation-register.template.md`, built first).

## The precedence typing it applies (its authority to drive)

When two representations disagree, `templates/precedence-typing.reference.md` names which is authoritative, so
the gate drives the non-authoritative one into line rather than guessing. That table is **baked-in gate
content, not withheld** — the drive discipline depends on it being inspectable. In summary: the **internal
map** is authoritative for topology/seats (the graph object is the source of truth); the **interface
designs** for the published token set; the **decision policy** for what a predicate means (but the predicate
must *bind*); the **diagram is a projection and is never authoritative**; the transformation map's **Write
grants table** is the authority shape's source-of-record; **"opens stay open"** governs opens; **the source drives its renders**. Where
no representation is authoritative — an unbindable predicate (INV-6), a register-silent authority question
(INV-10), a genuine is-it-open disagreement (INV-11) — the invariant is typed **`escalate-needed`**, not
driven.

## The invariant set it checks

The closed **twenty-one-invariant set** in `templates/coherence-matrix.template.md`, listed in the invariant
index (`checks/fixtures/coherence/topology-index.yaml` `closed_invariant_set`) the closure check reads. Fifteen
are structural, in §"closed fifteen-invariant set": the original twelve, plus INV-13 (every Test Suite case
points at real rules, real tokens and a real gate), INV-14 (each rule's code-or-judgment mark agrees with the
step it sits on) and INV-15 (every design artifact points at the upstream versions actually pinned). Each of the
fifteen names its **relation** (`set-equality` · `reference-resolves` · `token-binds` · `mirror` ·
`value-parity` · `render-reproduces`), its **representations**, and its **authoritative rep** — every one a
structural / reference match, never a semantic-goodness call. Six more, in §"The six reshape invariants"
(INV-16 to INV-21), are **judged on meaning** (relation `judged`): every outcome is truly delivered or justly
retired; every no-reshape line and every move's evidence holds; no required person step is skipped and no
action is lost; each restated-default write-grant NOTE is judged; each agent split cites a named reason; each
path-check warning is judged as routing, parallel work or a real skip. They read the reshape check's report
(`stages/future-state/reshape-check.json`) as input; a design with no transformation map holds all six with
the evidence "no transformation map: reshape invariants not applicable". The matrix's machine block also pins
the design set it was built against (`design_set: {path, sha256}`); a matrix built against another design set
is stale. **The inbound-control interface row (INV-4) is
a first-class hard gate:** an omission of the inbound-control interface or of its inbound direction is a
`violated`, never `n-a` (the analyst→agent inbound-interface cascade-miss locus observed in prior runs).

## The drive-to-coherence loop (the runtime procedure — the executor re-decides nothing)

1. **Diagnose in one pass.** Build the **disambiguation register** first (so the matrix keys off canonical
   ids, not raw overloaded strings). Run the **render-integrity precondition** (INV-12) — re-run each
   renderer, byte-diff, normalize any render defect by re-render and log it. Populate the **coherence matrix**
   across all twenty-one invariants (the fifteen structural and the six judged). Emit the **findings register** with each finding's proposed disposition (a
   `routed_to_owning_stage` correction with its precedence call, or a `named_escalation`). **No
   representation is edited in this pass.**
2. **The human authorizes each correction's shape — the coherence-gate pause.** The diagnostic pass **pauses**
   at a human authorization of the proposed correction shapes. **The orchestrator may not broaden an
   authorized shape mid-cycle:** if a correction reveals a need to change more than was authorized, that is a
   **new** diagnostic finding requiring fresh authorization, never a silent widening. Findings that need a
   **precedence call or a design decision** (the INV-6 / INV-10 / INV-11 residuals) each ride a **"Resolve a
   flagged blocker"** with `trigger: coherence-residual`; mechanical drives (a re-render, a stale-pointer
   re-point, a resolved stale enumeration) are authorized in the same pause as confirmations.
3. **Correct through the owning artifact's stage — never edit in place.** Each authorized correction
   **re-enters the owning stage's executor brief + rubric** and **re-runs that stage's executor + independent
   Codex review.** The route map (finding class → owning stage) is in
   `templates/findings-register.template.md`.
4. **Re-check after every reviewed correction.** After the owning-stage correction lands
   (`artifact.changed_verified` on the owning artifact), re-run the render-integrity precondition and
   re-populate the affected matrix rows; a driven violation that now holds flips to `status: routed`.
5. **Bound it to at most five total passes** (one diagnostic + up to four correction cycles). The cap is
   carried on the gate's tag as a stage-scoped `loop_policy`, not set by hand.
6. **Clearance rule.** The gate passes **only** when no inconsistency remains **or** every residual is a
   named escalation — i.e. every invariant row is `holds`, `routed`, or `escalated`, with **no `violated`
   left un-dispositioned.**

## How the loop reuses the existing remediation machinery (wired, not reinvented)

The loop machinery is the run's existing deterministic remediation controller — **not** the SDK
subprocess wrap + deny gate (which holds no cycle sequence, no loop policy, no escalate tier). This
gate wires onto the real seams by name. **Correcting through the owning stage is mechanical, not method
only:** a cleared stage accepts no further event until a `stage.reopened` (trigger `coherence-finding`,
`finding_ref` = the finding id, authorized by the batch authorization `human.decision` of the coherence-gate
pause, or by fast mode's recorded policy) lifts its terminality. The reopened stage runs its scoped remediation
cycle (its executor packet scoped to the finding, its checks, its independent review), re-clears, and the gate
diagnostic re-runs on a fresh design set. One exception: on the gate's pass that precedes stage 10's first
clearance, stage 10 is submitted but not cleared, so a finding routed to stage 10 needs no reopen (reopening an
open stage is refused); it is corrected by a new stage 10 submission scoped to the finding, checked and
reviewed, and the gate re-runs pinning it. After stage 10 has cleared, a stage 10 correction is a normal
reopen, and the reopened stage 10 re-clears only after the gate has passed on its new submission.

- **The bound is a loop-controller parameter, not a hardcode.** The gate carries, on its tag, a **stage-scoped
  `loop_policy: {max_cycles: 5, on_exit: escalate}`** (overriding the run-header default of 2, per the
  ratified "the cap is a parameter" decision in `reference/event-log-schema.md` → **Loop model**). The pipeline
  asset declares no cap for coherence, because coherence is a gate and no longer a stage; a stage-level
  declaration in the asset remains available to a real stage. **Cycle mapping: cycle 1 = the diagnostic pass**
  (its `executor.submitted` is the matrix + findings register); **cycles 2–5 = the four correction cycles** (each
  an authorized owning-stage correction + a re-check). `scripts/loop-controller.sh` resolves the cap through
  `scripts/_loop_policy.py` (an explicit stage-scoped `loop_policy` on an event wins, then a declaration in the
  asset, then the run header, then the built-in default; Demo still clamps to two) and routes the single next
  action. The same resolver supplies `max_cycles` wherever an escalation records it, and a malformed
  declaration is refused by the shared `loop_policy` validation.
- **`redraft` is re-interpreted as "route to the owning stage."** When `scripts/loop-controller.sh` returns
  `redraft` (verdict `remediate`, `cycle < max`), the gate does **not** have its own executor edit
  anything; the orchestrator reopens the **owning** stage (`stage.reopened`) and runs the authorized correction
  sub-cycle there (its executor brief + rubric + Codex review, via the normal `scripts/append-event.sh` /
  `scripts/run-check.sh` / reviewer path), then re-runs the gate diagnostic. This is the one adaptation of the
  generic loop.
- **The escalate tier is the existing one.** At `cycle == max_cycles` still-not-clear,
  `scripts/loop-controller.sh` emits `escalated{reason:"loop-exit", cycles_used, max_cycles, to:"human"}`;
  the gate additionally emits a **"Resolve a flagged blocker" `human.decision`** per still-open
  finding (`gate: mid-execution-steer`, `trigger: coherence-residual`). Residuals-at-cap **become named
  escalations, not a cap-burn.**
- **The serialized writer + re-derived gates are inherited unchanged.** Every correction, re-check, and
  clearance rides `scripts/append-event.sh` (the single serialized writer; it re-derives the SHA and re-runs
  the check before recording) and `scripts/run-check.sh` + `scripts/_checks.py` (it emits `check.passed`
  bound to the latest artifact SHA). The gate's pass **binds to its required check set** (see
  below); `artifact.changed_verified` guards that a correction actually landed; the time-of-clearance
  identity re-hash guards that nothing changed after review. The gate itself never takes `stage.cleared`: eval's
  clearance (and stage 11's and 12's executor starts) require the gate to be current, which the writer derives
  from the gate's checks, its review and the design set on disk.
- **Resume is derived, not authored:** a killed-and-restarted coherence pass resumes from the on-disk
  matrix + check state (the same event-log-derived resume the shipped `the orchestrator's front-door helper (retired)` performs); no
  trace is read to decide what runs next.

## The three mechanical checks this gate owns

All three are **structure + closed-set only** (they never judge whether the reconciled design is honest — the
review + sign-off own that), told their targets as **explicit arguments** (never self-detecting "am I a
design"), and **dry-validatable with no live agent / Company Brain**:

- **`checks/check-coherence-matrix-closure.sh COHERENCE_MATRIX FINDINGS_REGISTER INVARIANT_INDEX`** — every
  invariant in the closed set is `holds` / `routed` / `escalated`; no `violated` left un-dispositioned; every
  routed/escalated row resolves to a well-formed finding (a routed finding names its owning stage; an
  escalated finding is decision-ready).
- **`checks/check-predicate-binding-recheck.sh TOKEN_INDEX DECISION_POLICY`** — every decision-policy
  predicate token (declared `operative_tokens[]` + the dotted spans in each rule `condition`) still resolves
  to a published `<interface-id>.<field>` token by exact membership; an unbindable predicate fails the gate
  (its two ratified resolutions ride "Resolve a flagged blocker", never invented here).
- **`checks/check-design-set-current.sh DESIGN_SET`** — every file the design set pins exists with its pinned
  sha256 (exit 1 lists each changed file; exit 2 on a malformed design set). The closure check additionally
  refuses a matrix whose `design_set` pin is not the design set on disk.

## The required check set (the gate is current only with all three)

The gate's required check set is:

```
{ check-coherence-matrix-closure, check-predicate-binding-recheck, check-design-set-current }
```

each re-verified: the closure check bound to the gate's latest submitted matrix, the design-set check bound to
the design set the matrix pins, and the binding re-check bound to the pinned token index and decision policy
(its own arguments). The legacy `coherence-loop` stage keeps its two-check set so an older run can still clear it.

## Gate seating

- **"Resolve a flagged blocker"** fires when a coherence residual needs a **precedence call or a design
  decision** (the INV-6 / INV-10 / INV-11 residuals). It rides the fixed shape with **`trigger:
  coherence-residual`**, bound to the existing `human.decision.gate: mid-execution-steer` value; its payload
  carries `{trigger: coherence-residual, evidence_ref[], resolution, decision ∈ {accepted,
  changes-requested}, rationale, counterfactual, corrected_role}`. `resolution` for a coherence residual is
  the named open or the precedence call; `decision: accepted` when the resolution lets the gate proceed,
  `changes-requested` when it sends an artifact back for a corrected re-run.
- **No "Sign off the stage" for the gate itself.** The gate is never cleared as a stage (the writer refuses
  `stage.cleared`, `stage.accepted_with_residual` and `stage.blocked` on `design-coherence`); its pass is
  derived. At the cap (stage-scoped `loop_policy: {max_cycles: 5, on_exit: escalate}` on the gate tag's first
  event) every still-open finding becomes a named escalation and the gate can pass with those rows `escalated`
  (never `violated`); the residuals ride into stages 11 and 12 verbatim. Stage 10's own sign-off lands with the
  gate current. A reopen of a cleared stage needs the batch authorization `human.decision` of the gate's pause
  (or fast mode's recorded policy).
- **"Check the diagram reads true" does NOT apply** — the gate consumes the diagram, authors none.

The coherence-gate pause and each "Resolve a flagged blocker" firing are the **same human surface** at
different granularities: the pause authorizes the batch of proposed correction shapes; each residual needing
a real decision is one "Resolve a flagged blocker" within it.

## Session topology

`session_kind: spawnable` (declared in the gate's definition / `packet.authored` `session_kind` field): the
gate consumes the on-disk built design set and reuses the remediation-loop machinery; it needs **no live
Company Brain and no live agent.** Its routed corrections re-enter owning stages that may themselves be
interactive — but the gate itself is spawnable, and the orchestrator routes it as a fresh spawned
session.

## The exception trigger the gate emits

At run time the gate emits **`trigger: coherence-residual`** (a constant for this gate). The full five-value
exception `trigger` enum — `{non-convergence, unbindable-predicate, coherence-residual,
eval-instrument-failure, uncovered-escalation-trigger}` — is **architecture** (§3.2 /
`human-input-model.md`), not gate-local content; the gate's templates reference `coherence-residual` only.


# Stage — Run the design's rules on sample data before build

_Stage 11, key `pre-build-simulation`, deps `[eval]`. The method the stage's executor packet
(`templates/pre-build-simulation-brief.template.md`) and its withheld review template
(`tests/knowledge/control-templates/pre-build-simulation-rubric.template.md`) are drafted from. "Simulation" alone means running the
built agent at stage 12; this stage is always the pre-build simulation. SKILL.md's "Stage 11" subsection is the
front-door summary; the hand-off formats stage 12 builds against are the templates under
`pre-build-simulation-stage/templates/`._

## What the stage is

The pre-build simulation runs the design's decision rules against data **before any agent is built**, so the
business owner sees the rules run on samples and data-shape problems surface while they are cheap. It asks the
client for samples first and builds invented data only where there are none; it records, per system, what data
shape is actually known; it runs every rule on every case (every number and every numeric comparison in code); it
compares each case's result with the Test Suite's expected outcome; it routes each defect to the artifact that owns
it; and it ends with a plain statement of what was checked on real-shaped data, on assumed shapes, and not at all.

**It never refuses an agent.** A design that is hard to test still gets the full upfront work, and what could not
be checked is said plainly. A check here asks only for a stated reason every agent can give.

## Inputs (each pinned by path and sha from the design-coherence gate's design set)

| From | What | Used for |
|---|---|---|
| stage 9 decision rules | the policy's rule rows (each with its `runs_as` mark), gates, dispatch, escalation triggers, special cases, open register, guardrails; the binding manifest | the rules to run; the coverage denominators; the value opens stamped with provisional test values |
| stage 10 Test Suite | the cases per gate section with their `expected_outcome`, declared fixtures, `uncovered_open_rules`; calibration set and judge rubrics | the cases and what each should produce; the Test Suite's own ratified gaps |
| stage 8 interface design | the interface designs and the published token index | each token's type and source system (the layout data must project onto); derived tokens |
| stage 7 internal map | the internal-DAG graph | the parts, each step's `runs_on`, dispatch order and hand-offs (the walk) |
| stage 6 backbone | the build-spec | the agent's model (for judgment calls) and its systems |
| stage 1 snapshot | the frozen Company Brain Snapshot | layout evidence already observed (export headers, column names) |
| the gate | `output/coherence/*` | the gate must be current before the executor starts; its escalated residuals are carried into the manifest's `opens` |
| run inputs | `inputs/data-samples/<system-id>/…` and `inputs/data-samples/declaration.yaml` | the client's samples and layouts |

On a seeded start at stage 11 the design is brought: the design set pins it `brought`, and the gate runs once on
the brought design before the executor starts.

## The code-or-judgment mark (set at stage 9, used here)

Every stage 9 rule row carries `runs_as: code | judgment` with a `runs_as_basis`. **Code**: decidable from token
values by a fixed procedure. **Judgment**: needs interpretation no fixed procedure can state. Any other value is
allowed with a `runs_as_note`; stage 11 runs such a rule as code, as judgment, or not at all, and says which
(`other-marks.yaml`). **The code-first boundary:** no model is ever asked to add, subtract, count, compare two
numbers or test a threshold. A judgment rule with a numeric predicate carries `judgment_split` and runs in four
steps: the model *identifies* (item ids or verbatim text spans, never a computed number); code *computes* every sum,
difference and comparison; the model *judges* only the non-numeric remainder, given the computed results as facts
(or not at all when `judge` is null); code *combines* the outputs into `holds`. A judgment rule with no numeric
predicate runs as one step, with its `numeric_free_basis`. Stage 11 may challenge a mark with evidence, as a `mark`
defect routed back to stage 9.

## The data

1. **Ask for samples first.** The data request (`output/pre-build-simulation/data-request.md`) asks, per system the
   decisions read: a sample of real records as the system exports them; else the layout only (an export's header
   row and one redacted row, or an API response format); else "nothing yet", which is fine. The person relays it;
   the orchestrator never sends a message itself. What came back is recorded in
   `inputs/data-samples/declaration.yaml`, and a `human.decision` (gate `intake`) records that it is complete. Fast
   mode writes the request for later, reads what onboarding left, and the coverage statement says samples were not
   requested.
2. **Record each system's data shape** (`data-shape/<system-id>.yaml`): `real-sample` (a client sample is the
   evidence), `real-layout` (a client layout or Company Brain evidence), or `assumed` (the interface design only).
   Every token a rule reads maps to a field, a carried context, a derived value computed by the rules' code, or
   `absent` with the reason. A token the real layout cannot provide, read by a rule, is a `shape` defect. An
   `assumed` record carries an adapter risk, copied into `adapter-risks.yaml` and handed to stage 12, which resolves
   it or carries it into the handover.
3. **Build the case data.** Client samples first (the business owner's picks, then the rows each case describes);
   invented data only where there are none, always to the recorded layout. Generation is its own sub-step that never
   sees the decision policy, the expected outcomes, the judge rubrics or the calibration set; what it was given is
   recorded in `generation-inputs.json`. Every file is labelled `client_data` or `synthetic_data`, one label per file;
   a redacted client copy is still client data; "synthetic" never describes client-derived material.
4. **No answers in the data.** Data files hold raw records only: never a net, sum, difference, count, pairing,
   category, label, confidence, recommendation, evidence state or expected outcome. Every value a rule decides or a
   computed input produces is computed by the rules' code.
5. **Draft adapters** (one Python module per system under `adapters/`) read each recorded layout into token values, proven on the client
   sample where one exists; they compute no derived value. Stage 12 hardens them into the package's ingest adapter.

**The failure this prevents.** An agent built and tested on invented fixtures shaped as a flat table met its real
export only after the build: a report layout with banner rows, a metadata stanza per block carried onto the lines
below, a repeated header per block, subtotal rows, comma-formatted amounts with a sometimes-trailing minus, several
files per read to pool, and no item-text column at all. An adapter had to be built after the fact, and the rules
that read the missing column could not run as written. At stage 11, one header row and one redacted row are enough:
the data-shape record lists the layout's structure, the draft adapter must parse it now, and the absent column
raises a `shape` defect on every rule that reads it, routed to the decision rules or the interface design before any
build.

## How the rules run

- **Generated code, per rule set** (one stdlib-only Python module per rule set under `rules-code/`): one function per code rule, per
  computed input and per split rule's combine step, reading tokens only through the accessor (`t()`, `c()`, `p()`,
  `j()`), `Decimal` arithmetic, no float, no model. Each registry entry carries the sha of its canonical rule row
  (`scripts/prebuild-sim-rows.py --emit shas`), so an edited rule makes its code stale; the rule-trace check reads
  the functions' syntax to keep the code on the written rule.
- **The walk** (`walk.yaml`, derived by `scripts/prebuild-sim-walk.py`; any override carries its reason): per case,
  start at the entry, run each part's rules in order, follow the exit the results select. A later part reads what
  the earlier part produced (`chained`); when an upstream part diverged from the expected outcome, the downstream
  part runs `isolated` from its own inputs, so one upstream defect never hides downstream coverage. A value one part
  hands to the next is declared as a `derived` token of the data-shape record it is computed from; any number a later
  part needs from an identification is recomputed in code from the identified ids.
- **The runner** (`scripts/prebuild-sim-run.py`, reading `run.yaml`): every code step runs twice in the same
  invocation, and a difference is a `code` defect, never averaged. Each judgment prompt is linted before it is sent
  (a prompt that would ask the model to compute or compare a number is never sent; it becomes a `rule` defect) and
  sent `judgment_repeats` times (default 3) as separate requests; all agreeing is `stable`, any disagreement is
  `unstable`, the case is not comparable, and a `rule` defect says the wording lets the model decide either way. The
  write-once judgment cache keys each response by model, prompt and slot, so a re-run reads back independent
  repeats and never collapses them. A rule handled not-run is recorded with its reason and the walk continues past
  it. `cost.json` records every call's tokens and price; a `--no-model` run costs nothing and says so.
- **Provisional test values** (`parameters.yaml`): each value open whose rule runs gets a stamped value labelled
  "PROVISIONAL TEST VALUE - NOT A CLIENT VALUE"; a rule open runs as needs more information. A person confirms the
  values in detailed mode (gate `provisional-values`) before the round runs.

## Comparing, coverage and defects

- **Comparison** (`scripts/prebuild-sim-compare.py`, re-derived byte for byte by the results check): each case's end
  state and where it ended, every rule that must or must not fire, and every expected token; a must-escalate case
  that did not end escalated is a critical mismatch. Each case is a match, a mismatch (with every differing item),
  not comparable (with the reason) or not run (with the reason).
- **Coverage**: every rule, gate, escalation trigger and dispatch guard, exercised by which cases, on which data
  basis (the weakest among the systems it reads). A gap is reported, never failed: the Test Suite's own ratified
  gap, a rule decided by someone or something this check does not run, or else a `coverage` defect for the Test
  Suite.
- **Defects** (`defects.yaml`, each with its owner and one history entry per round): `code` (fixed inside this
  stage), `rule`, `mark` and `shape` (decision rules, or the interface design when the field itself must change),
  `case` and `coverage` (Test Suite), `mismatch` (by precedence: the policy is the correct-answer source, so an
  unambiguous policy makes the case wrong and an ambiguous one makes the rule wrong; a person decides where neither
  can tell), and `dispute` (the business owner's, reclassified in the next round).
- **The loop**: a bounded defect loop (`loop_policy: {max_cycles: 3, on_exit: escalate}`, one cycle = one round).
  Routing reopens each owning stage once per batch (`stage.reopened`, authorized by a person, or fast mode's recorded
  policy); the gate re-runs; the affected cases re-run, then the full suite once. At the cap the person lands
  `stage.accepted_with_residual` with every remaining defect as a residual, or reopens further; never a refusal.

## What the business owner sees

- **The coverage statement** (`coverage-statement.md`, its machine block equal to `coverage.yaml`): checked on
  real-shaped data; checked on assumed data shapes, with each adapter risk; not checked yet, each rule with its plain
  reason; values used for testing only; what was found and fixed, and what is still open. It never says the agent
  works, is ready, or passed.
- **The simulation page** (`scripts/render-prebuild-sim-page.py`, through `renderer/render_prebuild_sim_page.py`):
  a self-contained, offline view rendered from the submitted manifest, every file it pins and the recorded review
  records, adopting the close-the-books showcase's layout: the coverage statement at the top ("Not checked yet" and
  "Values used for testing only" always open), one card per case with its step trail, its evidence and the rules'
  result beside the expected outcome, the rules with their marks and bases, the open items, the adapter risks, the
  defects with their owners and history, earlier feedback, and the metered cost. It makes no network request and no
  model call, carries no copy of the rules and computes nothing; it holds only the reviewer's unsent draft. It is a
  sibling derived file, not the stage's inspection report, and no check or writer rule reads it. Its `section()` is
  what the run-long HTML of fast mode inlines at stage 11.
- **The feedback round-trip.** On the page the owner accepts or disputes each case (a dispute needs a note, kept
  verbatim) and may add samples (with their source, permission, redaction and provider); "Download feedback" writes
  one file pinned to the run, the design set, the final run and the submitted manifest. A chat request is written to
  the same shape with `source: chat`. The person drops it into `inputs/prebuild-review/`, and
  `scripts/prebuild-sim-review.py --check` then `--record` checks it against every rule of
  `pre-build-simulation-stage/templates/prebuild-review-feedback.rules.md` and records it.
- **Recording never touches a submitted round.** The review is recorded as its own record file
  (`reviews/review-<nnn>.yaml`) and one `human.decision` (gate `prebuild-simulation-review`); the submitted manifest
  and every file it pins stay byte-identical, and the recorder re-hashes them and treats any move as a recording bug.
  Disputes and added samples wait in the record until the next round folds them into `defects.yaml` and the
  declaration (`scripts/prebuild-sim-manifest.py --fold`), before that round's submission, which gets its own checks
  and its own independent review. In a Project run stage 11 clears only on the owner's accepted review of that exact
  submission; the writer's review floor (`scripts/_prebuild_review.py`) refuses clearance when any pinned file moved.

## Every agent shape

- **A pure script**: every rule code, one part, no model call and no cost; "judgment steps: none in this design".
- **A single agent with tools**: mostly code, a few judgment steps, each with a numeric predicate split.
- **Several parts with hand-offs**: a walk over the parts, chained, then isolated on divergence.
- **Many systems with little data**: most records `assumed`, each with its adapter risk; the full upfront work still
  runs, and most rules land under "checked on assumed data shapes".
- **A shape nobody has seen**: marks outside code and judgment are handled through `other-marks.yaml` (code,
  judgment or not-run, defaulting to not-run with its plain reason under "Not checked yet"); the walk falls back to one
  part per sub-agent; checks ask only for stated reasons; nothing is refused.

## Hands to stage 12

The submitted manifest and everything it pins (data and labels, data-shape records, adapter risks, draft adapters,
parameters, rules code and walk, per-case results and comparison, coverage, defects), with, beside it, the business
owner's review decision and its review record, or the manifest's open item `business-owner-review`. Stage 12 reuses
the data, reads the review decision, and carries every adapter risk to resolved or into the handover.

## Checks, gates, session

- Checks (each run with the manifest as its argument, from the run folder):
  `checks/check-prebuild-sim-structure.sh`, `checks/check-prebuild-sim-data-boundary.sh`,
  `checks/check-prebuild-sim-rule-trace.sh`, `checks/check-prebuild-sim-results.sh`. The gate must be current.
- Copy guard reach (settled decision N8, now built): the manifest plus the coverage statement and data-shape records
  it pins. Once this stage's worked references exist, `checks/check-reference-copy-guard.sh` scans the manifest and
  every file at the pointers `CARRIER_COMPANIONS` lists for this stage in `scripts/_reference_registry.py`
  (`/coverage/statement/path`, `/systems/*/data_shape/path`), each bound to its pinned sha256 inside the run folder;
  a pinned file it cannot bind stops the guard as a setup error. Stage 12 has no entry there yet.
- Where a person decides (detailed mode): the data declaration (`intake`), the provisional values (`provisional-values`), any
  expected outcome derived at this stage for an older Test Suite, each batch of reopens, and the business owner's
  review (`prebuild-simulation-review`), which is the stage's sign-off in Project mode. Fast mode proceeds with the
  open item `business-owner-review`, carried to stage 12.
- `session_kind: spawnable`. The model calls are a script run that needs a model key; in a build of the plugin
  itself they run only as a live proof in a separate dispatch.

# Stage — Build the agent and run it in a Sandbox

_Stage 12, key `sandbox-ship`, deps `[pre-build-simulation]`. The first cut: the spec template
(`sandbox-ship-stage/templates/sandbox-ship-spec.template.yaml`), the package builder and the four checks are built;
`sandbox-ship-stage/README.md` describes the spec field by field, and SKILL.md's "Hands to stage 12" is the
front-door summary._

## What the stage is

Stage 12 takes the cleared design and stage 11's results, builds the agent into one package inside the run's own
folder, runs the Test Suite in a Sandbox, and records the Simulation evidence and the handover. The Sandbox is a
runnable approximation of client conditions, never the client environment. Simulation is readiness evidence, not a
production claim, and passing tests clear no Production Readiness Gate.

## Inputs, spec and build

- **Inputs** are what stage 11 hands on: the submitted manifest and everything it pins, with the business owner's
  review decision and its review record, or the open item `business-owner-review`. Stage 12 reuses the data and
  draft adapters and carries every adapter risk to resolved or into the handover.
- **The spec** (`output/sandbox-ship/spec.yaml`, one per run) pins every design file and stage 11's manifest, and
  binds each system as access decides: `real` with a recorded grant, or a `stub` with a stated reason. Data from a
  real system is never called synthetic. The client name comes from the run header (`run.opened` event, `client`).
- **The package builder** (`scripts/sandbox-ship-build.py`) assembles the package from the spec; it needs no Docker.
- **The four checks**: `checks/check-sandbox-ship-spec.sh`, `checks/check-sandbox-ship-package.sh`,
  `checks/check-sandbox-ship-leak-scan.sh`, `checks/check-sandbox-ship-clearance.sh`.
- **Clearance** needs live evidence bound to the package hash, or the open item `live-sandbox-run` approved by a
  person.

## Not built yet, and where it stops

The worked reference, the live proofs and the production-launch templates are not built in this first cut; the
production-launch templates are handed on. The stage stops before production launch: a person decides launch,
outside this run.
