<!--
  Transformation stage — canonical EXECUTOR PACKET template (the brief-drafter instantiates this).
  (Plain-language stage name: "Agree the agent's role and authority". Internal tag: transformation.)

  ROLE ON THE HUMAN-INPUT MODEL: this stage runs an IN-PIPELINE CHALLENGE LOOP, not a single-pass
  executor + human-approve seam like backbone/internal-dag. A proposer classifies every `agent-taken`
  node and names sub-agent groups + handoffs (per the seed brief below, VERBATIM per the ratified design
  log); an independent opponent contests specific calls and tables a full counter-mapping; a judge
  reconciles. This is the same opponent/judge machinery this repo already runs for design-gate challenge
  loops (`{{ PLUGIN_ROOT }}/scripts/invoke-reviewer.sh`, invoked once per opponent/judge pass with the codex engine and a
  role-specific prompt — this brief references that mechanism as the stage's own runbook invokes it, the
  way other pipeline stages reference `checks/check-*.sh`; it is not duplicated here). The loop's reconciled output is a RECOMMENDATION, never
  authoritative on its own (plan v3 §A4) — the human future-state-remit gate always wins. The gate's human
  reviews the loop's recommended placement and records a `human.decision` event (`gate: future-state-remit`,
  a `decision` field — accept / amend / override — and a `rationale`), which is what downstream consumers
  (the backbone stage's `primitive_form` field) actually read — never the loop's raw output directly.

  THIS IS THE EXECUTOR PACKET, NEVER THE GRADING KEY. It is criteria-less by construction: it carries NO
  rubric, NO anchors, NO pass bar. The withheld grading key is
  never handed to the proposer or the opponent
  (the convention firewall: "study to the test" is banned).

  Parameterized: {{ CURRENT_STATE.* }} / {{ REMIT.* }} resolve at instantiation, each named + SHA-pinned;
  <describe: ...> is a slot filled from a named source. The instantiated artifact must pass
  check-packet-authoring and {{ PLUGIN_ROOT }}/checks/check-transformation-map-shape.sh (on the artifact it produces).
-->

# Agree the agent's role and authority · executor packet (in-pipeline challenge loop)

You are the PROPOSER in this stage's in-pipeline challenge loop. Classify every `agent-taken` node in the
transformation map, and name sub-agent groups + handoffs between them, from the cleared current-state
graph below. You do not review or grade your own work, and you never see the grading key.

**Session kind: spawnable.** This stage reads only on-disk evidence (the cleared current-state graph and
the ratified remit content), so the proposer pass runs as a fresh spawned executor. The opponent and judge
passes are independent, cross-engine sessions per this stage's own runbook (the same mechanism other
design-gate challenge loops in this repo already use).

## Inputs (read ONLY these)

- the transformation-map template: `{{ PLUGIN_ROOT }}/templates/transformation-map.template.md` · sha256 `{{ TEMPLATE.transformation_map.sha256 }}`
- the cleared **current-state graph**: `{{ CURRENT_STATE.path }}` · sha256 `{{ CURRENT_STATE.sha256 }}`
- the ratified **remit/authority gate content** for this run (if any exists yet — this stage's own loop
  now feeds the remit gate rather than presupposing it; see plan v3 §A4): `{{ REMIT.path }}` ·
  sha256 `{{ REMIT.sha256 }}`, or "not yet ratified — this loop's output is the input to that gate"
- the structural check: `{{ PLUGIN_ROOT }}/checks/check-transformation-map-shape.sh`
- the worked references for this stage, every level supplied: listed under "Worked references" below
  (run-relative copies under `references/<stage>/`, each with its sha256).
- the fixed stage conventions that always apply: `{{ PLUGIN_ROOT }}/reference/stage-conventions.md` (names rule, banned
  FDE vocabulary and their exemptions, the show-don't-assert rule, and this stage's own conventions).
- the active FDE working vocabulary — use its selected terms exactly.

Do NOT read any grading key, rubric, review brief, or anything under the run's `control/` directory.

## Worked references (shape exemplars, not sources)

{{ REFERENCES.block }}

## Seed brief (ratified wording — reproduced VERBATIM from the design log)

> This agent's shape sits somewhere on a spectrum — from a plain deterministic script, through a
> single agent surface making one kind of judgment call, to a multi-subagent system where separate
> units of agent work hand off to a person and to each other in sequence. Where this agent actually
> lands on that range is what this loop is for; don't force it toward one of those three points if
> the evidence lands somewhere between.
>
> For every `agent-taken` node in the transformation map, classify it as script-eligible or
> judgment-required (per the anchors above, grounded in what the current-state graph shows the
> person doing at that node — never assumed). Where judgment-required nodes cluster into units that
> run together and end in a handoff to a person whose decision gates a different, subsequent unit,
> name those units and the handoffs between them. Loop-back to the same unit is not a boundary.
>
> Report where this places the agent on the spectrum, and why, treating any initial read as a
> hypothesis to break, not a conclusion.

## Build

Instantiate `{{ PLUGIN_ROOT }}/templates/transformation-map.template.md` into the transformation map for this run, honoring
every invariant below:

- **Script-eligibility test** (per `agent-taken` node; the precision bar is stated in
  `{{ PLUGIN_ROOT }}/reference/stage-conventions.md` → Script-eligibility precision bar, and this is the same test): eligible
  if the node's work is a string match, a
  lookup, or a basic arithmetic operation, fully specified by what the current-state graph shows the
  person doing there. Judgment-required for anything else — fuzzy matching, weighing conflicting evidence,
  or discretion the documentation can't fully specify.
- **Sub-agent boundary rule**: a new boundary exists only where a run of agent work (one LLM call or
  several chained with deterministic gates) ends in a handoff to a person whose decision gates a
  *different*, subsequent unit of agent work. A person sending work back to the *same* unit for rework is
  not a boundary — a write-back interaction at the interface, not a second sub-agent.
- **Layer on the existing base taxonomy; do not reconcile it.** Keep this run's own base-disposition
  column exactly as it already is (canonical `agent-taken` / dogfooding's `deterministic-code-executed` /
  a client's own letter codes such as A/R/S/E/U). The two new columns (`Shape Classification`, `Sub-Agent Group`) and the
  `## Sub-Agent Groups` section are additive.
- **Honesty bar.** Every `Shape Classification` / `Sub-Agent Group` / `Basis` / `Handoff out (gating
  decision)` value traces to the current-state graph. Fold the evidence state (Observed / Assumed /
  Contradictory / needs_more_information) into the prose cell, matching the convention every real run
  already uses. Invent nothing — an unclassifiable row is a flagged gap, never a guessed classification.
- **Reshape first, then assign (0400).** Before the per-node table, fill three required sections, in this
  order: `## Reshape moves` (header `| Move | Kind | Current steps | Future steps | Evidence | State | How the
  outcome is kept |`; ids `RM-<n>`; Kind one of the eight move kinds the template lists; Evidence cites at least
  one snapshot source or issue id, never step ids alone; State `Assumed` or `not adopted: <reason>`);
  `### No reshape` directly under it (one `- <kind>: <reason>` bullet per kind with no move); and `## Outcomes`
  (header `| Current ending | Future ending | Reason and evidence |`; one row per current-state ending; Future
  ending a future-state end-event id or `retired`). Then assign: the per-node table's last column, `Fate`, gives
  each current-state node's fate (`kept`, `assigned`, `merged into RM-<n>`, `removed by RM-<n>`, `split by
  confirm/enter`); section 3 lists every planned future step with no current node as `| Future step | Added by |`
  (an `RM-<n>` or `confirm/enter`), counted separately from the tally.
- **Write grants `Recipient` column.** The Write grants table carries `Recipient` as its 13th and last column,
  after `Access` — the one ruled amendment to 0399's table: `internal` / `outside` / `unclear` on `notice` rows,
  `n/a` on every other row. Floor flags: a `mechanical` row written by a person needs Key control `Yes`; a
  `notice` written by a person never goes to an `internal` recipient; a person row whose Next rung only restates
  the default (the agent enters confirmed values) is printed as a NOTE.
- **This loop's output is advisory, not final.** State explicitly, in the artifact's own closing section,
  that the derived placement is this loop's recommendation to the future-state-remit gate, and that the
  gate's human may accept, amend, or override it.

## Verify before returning (mechanical self-checks)

- run `{{ PLUGIN_ROOT }}/checks/check-transformation-map-shape.sh <transformation-map.md>` and confirm it exits 0 (every
  agent-taken row classified; no group on a script-eligible row; group declarations reconcile; no
  dangling/self-referential handoff; no placeholder basis/gating-decision text; the reshape sections, the
  Recipient column and the floor flags hold) and record the PLACEMENT
  it reports — this is the recommendation the remit gate reviews, not a value you assert independently;
- confirm no client-traceable string and no banned FDE term is present, per the names rule and the
  banned-vocabulary list in `{{ PLUGIN_ROOT }}/reference/stage-conventions.md` (exemptions (a)-(d) apply only as stated there);
- when "Worked references" lists references: run `{{ PLUGIN_ROOT }}/checks/check-reference-use-record.sh --stage transformation-plan <output>`
  and `{{ PLUGIN_ROOT }}/checks/check-reference-copy-guard.sh --stage transformation-plan <output>` from the run folder and confirm both exit 0.
- **Show, don't assert** (`{{ PLUGIN_ROOT }}/reference/stage-conventions.md`): back every step above in the return with the
  command run, its exit code and the relevant output, in a `## Verification` section of the return, one
  entry per step. A bare "confirmed" does not count; a step that could not run is reported as not run.

## Reproducibility bar + fidelity filter

A fresh opponent, reading the current-state graph and this artifact alone, must be able to contest specific
classification/grouping calls and table a full counter-mapping without re-deriving the script-eligibility
test or the boundary rule from scratch — both are stated above, verbatim from the ratified seed brief.

## Return (`{{ RETURN.path }}`)

What you built, plus the artifact identity (path + sha256 + mtime) in a **separate post-write receipt** —
never embed a file's own final SHA-256 inside it. No self-grade.
The return carries the `## Verification` section required above.
